/* Monitor v4l2loopback's client-usage event, without reading camera frames.
 * Event contract verified against Debian v4l2loopback 0.15.4 source.
 * Emits a boolean: the driver's field is not an actual reader count.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define CLIENT_USAGE (V4L2_EVENT_PRIVATE_START + 0x08E00000 + 1)
int main(int argc, char **argv)
{
    const char *path = argc > 1 ? argv[1] : "/dev/video10";
    unsigned limit = argc > 2 ? strtoul(argv[2], NULL, 10) : 0;
    unsigned received = 0;
    int fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) { perror("open camera monitor"); return 1; }
    struct v4l2_event_subscription subscription = {
        .type = CLIENT_USAGE, .flags = V4L2_EVENT_SUB_FL_SEND_INITIAL,
    };
    if (ioctl(fd, VIDIOC_SUBSCRIBE_EVENT, &subscription)) {
        perror("subscribe camera usage"); close(fd); return 1;
    }
    while (!limit || received < limit) {
        struct pollfd descriptor = { .fd = fd, .events = POLLPRI };
        /* A finite diagnostic request has a deadline. The long-lived service
         * needs only usage/removal events, with no periodic idle wakeup. */
        int ready = poll(&descriptor, 1, limit ? 15000 : -1);
        if (ready < 0 && errno == EINTR) continue;
        if (ready == 0 && !limit) continue;
        if (ready <= 0) { close(fd); return 2; }
        if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            fputs("camera usage channel unavailable\n", stderr); close(fd); return 1;
        }
        struct v4l2_event event;
        memset(&event, 0, sizeof(event));
        if (ioctl(fd, VIDIOC_DQEVENT, &event)) {
            if (errno == EAGAIN) continue;
            perror("read camera usage"); close(fd); return 1;
        }
        if (event.type != CLIENT_USAGE) continue;
        uint32_t active;
        memcpy(&active, &event.u, sizeof(active));
        printf("{\"camera_reader_active\":%s}\n", active ? "true" : "false");
        fflush(stdout); received++;
    }
    close(fd); return 0;
}
