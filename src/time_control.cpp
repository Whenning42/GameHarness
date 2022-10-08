// This library is used to control the time acceleration of another process.
//
// To setup time acceleration for the client process run:
//   $ LD_PRELOAD=./time_control.so TIME_CHANNEL=n my_process
//
// To control the time acceleration from the controller process use the interface in time_writer.py.

#include <cassert>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <iostream>
#include <utime.h>
#include <time.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <semaphore.h>
#include <math.h>
#include <mutex>
#include <atomic>

const char* FIFO = "/tmp/time_control";
const char* CHANNEL_VAR_NAME = "TIME_CHANNEL";

const int NUM_CLOCKS = 4;
const float INITIAL_SPEED = 1;

const int MILLION = 1000000;
const int BILLION = 1000000000;

// Intercepted functions
// - time
// - gettimeofday
// - clock_gettime
// - clock
// - nanosleep
// - usleep
// - sleep
// - clock_nanosleep
// x pthread_cond_timedwait
// x sem_timedwait

// A helper macro that takes in a function name "func" and declares a
// pointer type "PFN_func" of that function;s type.
#define PFN_TYPEDEF(func) typedef decltype(&func) PFN_##func

// A helper macro that takes in a function name "func" and dlsym loads the
// function if it's global value is nullptr. This requires real_func to be
// declared globally.
#define LAZY_LOAD_REAL(func) if(!real_##func) { \
    real_##func = (PFN_##func)dlsym(RTLD_NEXT, #func); \
}

PFN_TYPEDEF(time);
// gettimeofday's decltype has a no_except that throws a warning
// when we use PFN_TYPEDEF.
typedef int (*PFN_gettimeofday)(timeval*, void*);
PFN_TYPEDEF(clock_gettime);
PFN_TYPEDEF(clock);
PFN_TYPEDEF(nanosleep);
PFN_TYPEDEF(usleep);
PFN_TYPEDEF(sleep);
PFN_TYPEDEF(clock_nanosleep);

// Global
std::atomic<PFN_time> real_time = nullptr;
std::atomic<PFN_gettimeofday> real_gettimeofday = nullptr;
std::atomic<PFN_clock_gettime> real_clock_gettime = nullptr;
std::atomic<PFN_clock> real_clock = nullptr;
std::atomic<PFN_nanosleep> real_nanosleep = nullptr;
std::atomic<PFN_usleep> real_usleep = nullptr;
std::atomic<PFN_sleep> real_sleep = nullptr;
std::atomic<PFN_clock_nanosleep> real_clock_nanosleep = nullptr;

// Statically initialize our global pointers.
class InitPFNs {
 public:
  InitPFNs() {
    LAZY_LOAD_REAL(time);
    LAZY_LOAD_REAL(gettimeofday);
    LAZY_LOAD_REAL(clock_gettime);
    LAZY_LOAD_REAL(clock);
    LAZY_LOAD_REAL(nanosleep);
    LAZY_LOAD_REAL(usleep);
    LAZY_LOAD_REAL(sleep);
    LAZY_LOAD_REAL(clock_nanosleep);
  }
};

// Guarded by write lock in fake_time.
int speed_file = 0;

int test_update = 0;
float new_speedup = 0;

struct ClockState {
  float speedup;
  timespec clock_origins_real[4];
  timespec clock_origins_fake[4];
};

timespec operator-(const timespec& t1, const timespec& t0) {
  timespec out;
  int64_t sec_delta = t1.tv_sec - t0.tv_sec;
  int64_t nsec_delta = t1.tv_nsec - t0.tv_nsec;
  if (nsec_delta > BILLION) {
    sec_delta += 1;
    nsec_delta -= BILLION;
  } else if (nsec_delta < 0) {
    sec_delta -= 1;
    nsec_delta += BILLION;
  }
  out.tv_sec = sec_delta;
  out.tv_nsec = nsec_delta;
  return out;
}

std::ostream& operator<<(std::ostream& o, const timespec& t) {
  o << "tv_sec: " << t.tv_sec << " " << "tv_nsec: " << t.tv_nsec;
  return o;
}

timespec operator*(const timespec& t, double s) {
  timespec out;

  double s_sec = t.tv_sec * s;
  double s_nsec = t.tv_nsec * s;

  int64_t s_sec_int = s_sec;
  double s_sec_dec = s_sec - s_sec_int;
  int64_t s_nsec_int = s_nsec + BILLION * s_sec_dec;

  int64_t mod = (s_nsec_int % BILLION + BILLION) % BILLION;

  s_sec_int += (s_nsec_int - mod) / BILLION;
  s_nsec_int = mod;

  out.tv_sec = s_sec_int;
  out.tv_nsec = s_nsec_int;
  return out;
}

// Helpers functions.
namespace {

// To reduce to number of clocks we have to fetch each time we change our speedup,
// we only use a few real clocks, and redirect calls for the other clock, to this
// set of base clocks (REALTIME, MONOTONIC, PROCESS_CPUTIME_ID, THREAD_CPUTIME_ID).
int base_clock(int clkid) {
  switch (clkid) {
    case CLOCK_REALTIME:
      return CLOCK_REALTIME;
    case CLOCK_MONOTONIC:
      return CLOCK_MONOTONIC;
    case CLOCK_PROCESS_CPUTIME_ID:
      return CLOCK_PROCESS_CPUTIME_ID;
    case CLOCK_THREAD_CPUTIME_ID:
      return CLOCK_THREAD_CPUTIME_ID;
    case CLOCK_MONOTONIC_RAW:
      return CLOCK_MONOTONIC;
    case CLOCK_REALTIME_COARSE:
      return CLOCK_REALTIME;
    case CLOCK_MONOTONIC_COARSE:
      return CLOCK_MONOTONIC;
    case CLOCK_BOOTTIME:
      return CLOCK_MONOTONIC;
    case CLOCK_REALTIME_ALARM:
      return CLOCK_REALTIME;
    case CLOCK_BOOTTIME_ALARM:
      return CLOCK_MONOTONIC;
    default:
      return -1;
  }
}

timespec operator+(const timespec& t1, const timespec& t0) {
  timespec neg_t0;
  neg_t0.tv_sec = -t0.tv_sec;
  neg_t0.tv_nsec = -t0.tv_nsec;
  return t1 - neg_t0;
}

timespec operator/(const timespec& t, double s) {
  return t * (1 / s);
}

timespec fake_time_impl(int clk_id, const ClockState* clock) {
  clk_id = base_clock(clk_id);
  timespec real;
  real_clock_gettime(clk_id, &real);
  // std::cout << "Real time fetched: " << real << std::endl;
  timespec real_delta = real - clock->clock_origins_real[clk_id];
  // std::cout << "Real baseline:     " << clock->clock_origins_real[clk_id] << std::endl;
  // std::cout << "Fake baseline:     " << clock->clock_origins_fake[clk_id] << std::endl;
  return clock->clock_origins_fake[clk_id] + real_delta * clock->speedup;
}

void update_speedup(float new_speed, const ClockState* read_clock, ClockState* write_clock, bool should_init = false) {
  ClockState new_clock;
  new_clock.speedup = new_speed;
  for (int clk_id = 0; clk_id < NUM_CLOCKS; clk_id++) {
    real_clock_gettime(clk_id, &new_clock.clock_origins_real[clk_id]);
    timespec fake;
    if (should_init) {
      real_clock_gettime(clk_id, &fake);
    } else {
      fake = fake_time_impl(clk_id, read_clock);
    }
    // std::cout << "Real baseline new: " << new_clock.clock_origins_real[clk_id] << std::endl;
    // std::cout << "Fake baseline new: " << fake << std::endl;
    new_clock.clock_origins_fake[clk_id] = fake;
  }
  *write_clock = new_clock;
}

bool get_new_speed(float* new_speed) {
  bool changed_speed = false;
  if (!speed_file) {
    std::string file_name = FIFO;
    if (std::getenv(CHANNEL_VAR_NAME)) {
      file_name += std::getenv(CHANNEL_VAR_NAME);
    }
    // printf("Opening speed file: %s\n", file_name.c_str());

    speed_file = open(file_name.c_str(), O_RDONLY | O_NONBLOCK);
    if (speed_file == -1) {
      printf("Failed to open speed file with errno: %d\n", errno);
      return false;
    }
  } else {
    // printf("Speed file is open.\n");
  }

  char buf[64];
  lseek(speed_file, 0, SEEK_SET);
  ssize_t read_num = read(speed_file, &buf, 64);
  // printf("Read %d bytes.\n", read_num);
  if (read_num < 0) {
    printf("Failed read with errno: %d\n", errno);
  }
  if (read_num > 0) {
    // printf("Reading float at offset: %d\n", read_num - 4);
    *new_speed = *(float*)(buf + read_num - 4);
    // printf("Found new speed: %f\n.", *new_speed);
    changed_speed = true;
  }
  return changed_speed;
}

ClockState init_clock() {
  ClockState clock;
  update_speedup(INITIAL_SPEED, /*read_clock=*/nullptr, &clock, /*should_init=*/true);
  return clock;
}

timespec fake_time(int clk_id) {
  static InitPFNs init_static_pfns;
  int orig_errno = errno;

  static ClockState clocks[2] = {init_clock(), init_clock()};
  static std::atomic<uint64_t> read_clock = 0;
  static std::atomic<int> clock_tag;

  // Try updating fake time.
  {
    static std::atomic<bool> write_lock = false;
    static std::atomic<uint64_t> write_clock = 1;

    // If we can't get write lock, break.
    bool was_locked = write_lock.exchange(true);
    if (was_locked) {
      goto cont;
    }

    float new_speed;
    bool change_speed = get_new_speed(&new_speed);
    if (test_update) {
      change_speed = true;
      new_speed = new_speedup;
      test_update = 0;
    }

    if (change_speed) {
        uint64_t old_read_clock = read_clock.load();

        // Write to the write clock's state.
        update_speedup(new_speed, &clocks[old_read_clock % 2], &clocks[write_clock % 2]);

        // Move the newly written clock into read_clock and make the other clock the write_clock.
        read_clock.store(write_clock);
        write_clock = write_clock + 1;
    }

    // Release the write lock.
    write_lock.store(false);
  }
cont:

  clk_id = base_clock(clk_id);
  timespec fake;
  uint64_t local_clock;
  do {
    local_clock = read_clock.load();
    fake = fake_time_impl(clk_id, &clocks[local_clock % 2]);
  } while (local_clock != read_clock.load());

  errno = orig_errno;
  return fake;
}
}  // namespace

time_t time(time_t* arg) {
  timespec tp = fake_time(CLOCK_REALTIME);
  return tp.tv_sec;
}

// NOTE: The error semantics here are a little off.
int gettimeofday(struct timeval *tv, struct timezone *tz) {
  timespec tp = fake_time(CLOCK_REALTIME);
  tv->tv_sec = tp.tv_sec;
  tv->tv_usec = tp.tv_nsec / 1000;
  return 0;
}

int clock_gettime(clockid_t clk_id, struct timespec *tp) {
  *tp = fake_time(clk_id);
  return 0;
}

clock_t clock() {
  timespec tp = fake_time(CLOCK_PROCESS_CPUTIME_ID);
  return (tp.tv_sec + (double)(tp.tv_nsec) / BILLION) * CLOCKS_PER_SEC;
}

// // NOTE: The error semantics for sleep functions isn't preserved in these wrappers.
// int nanosleep(const struct timespec* req, struct timespec* rem) {
//   try_updating_speedup();
//   LAZY_LOAD_REAL(nanosleep);
//   float speedup = global_clock_state.load().speedup;
//   timespec goal_req = *req / speedup;
//   timespec goal_rem;
//   int ret = real_nanosleep(&goal_req, &goal_rem);
//   if (rem) {
//     *rem = goal_rem * speedup;
//   }
//   return ret;
// }
//
// int usleep(useconds_t usec) {
//   timespec orig_nanosleep;
//   orig_nanosleep.tv_sec = usec / MILLION;
//   orig_nanosleep.tv_nsec = (uint64_t)(usec * 1000) % BILLION;
//   // Time speedup happens in the call to our override nanosleep.
//   nanosleep(&orig_nanosleep, nullptr);
//   return 0;
// }
//
// unsigned int sleep(unsigned int seconds) {
//   LAZY_LOAD_REAL(nanosleep);
//   timespec sleep;
//   sleep.tv_sec = seconds;
//   sleep.tv_nsec = 0;
//   nanosleep(&sleep, nullptr);
//   return 0;
// }
//
// int clock_nanosleep(clockid_t clockid, int flags, const struct timespec* request, struct timespec* remain) {
//   LAZY_LOAD_REAL(clock_nanosleep);
//   float speedup = global_clock_state.load().speedup;
//   timespec goal_req = *request / speedup;
//   timespec goal_rem;
//   int ret = real_clock_nanosleep(clockid, flags, &goal_req, &goal_rem);
//   *remain = goal_rem * speedup;
//   return ret;
// }

void __set_speedup(float speedup) {
  test_update = 1;
  new_speedup = speedup;
}

void __sleep_for_nanos(uint64_t nanos) {
  LAZY_LOAD_REAL(nanosleep);
  timespec n;
  n.tv_sec = nanos / BILLION;
  n.tv_nsec = nanos % BILLION;
  real_nanosleep(&n, nullptr);
}

int __real_clock_gettime(int clkid, timespec* t) {
  LAZY_LOAD_REAL(clock_gettime);
  return real_clock_gettime(clkid, t);
}
