// The UserKeyboard class wraps a user's keyboard input exposing the user's
// keyboard state and allows disabling the users keyboard.
// Users can press Super + Alt + Q to disable the UserKeyboard class to regain
// input control.

#include <array>
#include <atomic>
#include <cstdint>
#include <string>
#include <thread>

const std::string kKeyboardRegex = "AT Translated";

// For internal use only.
struct Devices {
  int master_pointer;
  int master_keyboard;
  int device_keyboard;
};

class UserKeyboard {
  public:
    UserKeyboard();
    ~UserKeyboard();

    // Toggle the user's keyboard input. Useful for pausing user input when applying
    // perturbation to their tasks.
    void Disable();
    void Enable();

    // Returns whether the user has disabled this UserKeyboard instance by pressing
    // Super + Alt + Q.
    bool IsHalted();

    std::array<uint8_t, 256> KeyState() { return key_state_; }

  private:
    void StartLoop();

    std::thread loop_;
    Devices devices_;
    std::atomic<bool> disabled_;
    std::atomic<bool> running_;
    std::atomic<bool> is_halted_;
    std::array<uint8_t, 256> key_state_;
};
