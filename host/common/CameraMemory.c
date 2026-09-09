/* Shared by the macOS producer and Linux reader. No camera/device APIs here.
 * Three host-owned slots. A descriptor leases a slot until the next ack;
 * generation and commit checks reject revocation during stop/reconnect.
 */
#define _GNU_SOURCE 1
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAP_BYTES (8u * 1024 * 1024)
#define SLOT_BYTES (2u * 1024 * 1024)
#define FRAME_BYTES (1280u * 720 * 3 / 2)
_Static_assert(ATOMIC_LLONG_LOCK_FREE == 2, "Camera commits need lock-free atomics");

void *ashacky_camera_map(const char *path, int writable)
{
    int fd = open(path, (writable ? O_RDWR : O_RDONLY) | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return NULL;
    struct stat st;
    void *p = MAP_FAILED;
    if (!fstat(fd, &st) && st.st_size == MAP_BYTES)
        p = mmap(NULL, MAP_BYTES, PROT_READ | (writable ? PROT_WRITE : 0), MAP_SHARED, fd, 0);
    close(fd);
    return p == MAP_FAILED ? NULL : p;
}
void ashacky_camera_unmap(void *p) { if (p) munmap(p, MAP_BYTES); }
static unsigned char *slot_at(void *p, unsigned slot)
{ return (unsigned char *)p + 4096 + slot * SLOT_BYTES; }

void ashacky_camera_clear(void *p)
{
    for (unsigned i = 0; i < 3; ++i) {
        unsigned char *s = slot_at(p, i);
        atomic_store_explicit((_Atomic uint64_t *)s, 0, memory_order_seq_cst);
        memset(s + 8, 0, SLOT_BYTES - 8);
    }
}

int ashacky_camera_publish(void *p, unsigned slot, const unsigned char *generation,
    uint64_t sequence, const void *y, size_t y_stride, const void *uv, size_t uv_stride)
{
    if (!p || slot >= 3 || !sequence || !generation || !y || !uv || y_stride < 1280 || uv_stride < 1280) return -1;
    unsigned char *s = slot_at(p, slot);
    atomic_store_explicit((_Atomic uint64_t *)s, 0, memory_order_seq_cst);
    memcpy(s + 8, generation, 16);
    for (size_t row = 0; row < 720; ++row)
        memcpy(s + 64 + row * 1280, (const unsigned char *)y + row * y_stride, 1280);
    for (size_t row = 0; row < 360; ++row)
        memcpy(s + 64 + 1280 * 720 + row * 1280, (const unsigned char *)uv + row * uv_stride, 1280);
    atomic_store_explicit((_Atomic uint64_t *)s, sequence, memory_order_release);
    return 0;
}

int ashacky_camera_copy(void *p, unsigned slot, const unsigned char *generation,
    uint64_t sequence, void *destination, size_t bytes)
{
    if (!p || slot >= 3 || !sequence || !generation || !destination || bytes != FRAME_BYTES) return -1;
    unsigned char *s = slot_at(p, slot);
    if (atomic_load_explicit((_Atomic uint64_t *)s, memory_order_acquire) != sequence ||
        memcmp(s + 8, generation, 16)) return -2;
    memcpy(destination, s + 64, bytes);
    atomic_thread_fence(memory_order_seq_cst);
    if (atomic_load_explicit((_Atomic uint64_t *)s, memory_order_acquire) != sequence ||
        memcmp(s + 8, generation, 16)) {
        memset(destination, 0, bytes);
        return -2;
    }
    return 0;
}
