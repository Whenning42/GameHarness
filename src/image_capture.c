#include "image_capture.h"

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XShm.h>
#include <assert.h>
#include <stdlib.h>
#include <sys/shm.h>
#include <sys/stat.h>

#include <stdio.h>

struct ImageCapture {
  Display *display;
  int screen;
  XImage *image;
  XShmSegmentInfo shminfo;
  void* handler;
};

capture_t SetupImageCapture(int width, int height) {
  struct ImageCapture* capture = malloc(sizeof(struct ImageCapture));

  Display *display = XOpenDisplay(NULL);
  int screen = XDefaultScreen(display);

  XImage *image =
      XShmCreateImage(display, DefaultVisual(display, screen), 24, ZPixmap,
                      NULL, &capture->shminfo, width, height);

  // Creates a new shared memory segment large enough for the image with read
  // write permissions
  capture->shminfo.shmid = shmget(IPC_PRIVATE, image->bytes_per_line * image->height,
                         IPC_CREAT | S_IRWXU);
  capture->shminfo.readOnly = False;

  assert(capture->shminfo.shmid != -1);

  image->data = (char *)shmat(capture->shminfo.shmid, NULL, 0);
  capture->shminfo.shmaddr = image->data;

  XShmAttach(display, &capture->shminfo);
  capture->display = display;
  capture->screen = screen;
  capture->image = image;

  return capture;
}

#include <time.h>
char *CaptureImage(const capture_t capture_h, Window window) {
  const struct ImageCapture* capture = capture_h;

  clock_t start = clock();
  XShmGetImage(capture->display, window, capture->image, 0, 0, AllPlanes);
  double duration = ((double)(clock() - start)) / CLOCKS_PER_SEC;

  return capture->image->data;
}

void CleanupImageCapture(capture_t capture_h) {
    struct ImageCapture* capture = capture_h;
    assert(XShmDetach(capture->display, &capture->shminfo));
    XDestroyImage(capture->image);
    shmdt(capture->shminfo.shmaddr);
    shmctl(capture->shminfo.shmid, IPC_RMID, 0);
    free(capture);
}

int (*global_mim)(Display*, XErrorEvent*, void*) = NULL;
void* global_py_handler = NULL;

#include <stdio.h>
int _call_global_handler(Display* display, XErrorEvent* error) {
    return global_mim(display, error, global_py_handler);
}

void SetErrorHandler(OnErrorMIM mim, void* on_error_py) {
  global_mim = mim;
  global_py_handler = on_error_py;
  XSetErrorHandler(_call_global_handler);
}
