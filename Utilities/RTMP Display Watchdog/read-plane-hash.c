/* Reads the actual pixel content of whichever DRM plane currently has a
 * non-zero framebuffer bound (in this deployment, that's always VLC's
 * plane -- the other planes sit unused with fb=0), and prints a hash of
 * the Y (luma) plane's raw bytes to stdout.
 *
 * This exists because sampling the plane's *framebuffer ID* (as the
 * watchdog originally did) can't tell "genuinely new frame" apart from
 * "the same frame re-copied into the next buffer slot to keep the
 * render loop's timing going" -- confirmed live on 2026-09-04: the fb ID
 * kept cycling through buffer slots the entire time the picture was
 * actually frozen. Hashing the real pixel content can't be fooled by
 * that, since a repeated frame hashes identically regardless of which
 * buffer slot it landed in.
 *
 * Reads via PRIME/dma-buf export + mmap, with an explicit
 * DMA_BUF_SYNC_START/END around the read for cache coherency -- without
 * that, a stale cached copy could be read back instead of what the GPU
 * actually wrote most recently, which would silently break exactly the
 * check this program exists to provide.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <linux/dma-buf.h>

int main(void) {
    int fd = open("/dev/dri/card0", O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "open card0: %s\n", strerror(errno));
        return 1;
    }

    /* Without this, drmModeGetPlaneResources only returns legacy
     * cursor/primary planes -- the overlay plane VLC actually renders to
     * won't show up at all. */
    drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1);

    drmModePlaneResPtr planes = drmModeGetPlaneResources(fd);
    if (!planes) {
        fprintf(stderr, "drmModeGetPlaneResources failed\n");
        return 1;
    }

    uint32_t fb_id = 0;
    for (uint32_t i = 0; i < planes->count_planes; i++) {
        drmModePlanePtr p = drmModeGetPlane(fd, planes->planes[i]);
        if (p && p->fb_id != 0) {
            fb_id = p->fb_id;
            drmModeFreePlane(p);
            break;
        }
        if (p) drmModeFreePlane(p);
    }
    drmModeFreePlaneResources(planes);

    if (fb_id == 0) {
        fprintf(stderr, "no plane with a bound framebuffer found\n");
        return 2;
    }

    drmModeFB2Ptr fb2 = drmModeGetFB2(fd, fb_id);
    if (!fb2) {
        fprintf(stderr, "drmModeGetFB2 failed: %s\n", strerror(errno));
        return 3;
    }

    if (fb2->handles[0] == 0) {
        fprintf(stderr, "no handle on framebuffer (need CAP_SYS_ADMIN? run as root)\n");
        return 4;
    }

    int prime_fd = -1;
    if (drmPrimeHandleToFD(fd, fb2->handles[0], DRM_CLOEXEC | DRM_READ_ONLY, &prime_fd) != 0) {
        fprintf(stderr, "drmPrimeHandleToFD failed: %s\n", strerror(errno));
        return 5;
    }

    /* Y (luma) plane only -- sufficient to detect real picture changes,
     * and cheaper than also reading the two chroma planes. */
    size_t y_size = (size_t)fb2->pitches[0] * fb2->height;

    void *map = mmap(NULL, y_size, PROT_READ, MAP_SHARED, prime_fd, fb2->offsets[0]);
    if (map == MAP_FAILED) {
        fprintf(stderr, "mmap failed: %s\n", strerror(errno));
        return 6;
    }

    struct dma_buf_sync sync_start = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ };
    ioctl(prime_fd, DMA_BUF_IOCTL_SYNC, &sync_start);

    /* FNV-1a over the raw luma bytes. */
    uint64_t hash = 1469598103934665603ULL;
    const uint8_t *bytes = (const uint8_t *)map;
    for (size_t i = 0; i < y_size; i++) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }

    struct dma_buf_sync sync_end = { .flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ };
    ioctl(prime_fd, DMA_BUF_IOCTL_SYNC, &sync_end);

    printf("%016lx\n", (unsigned long)hash);

    munmap(map, y_size);
    close(prime_fd);
    drmModeFreeFB2(fb2);
    close(fd);
    return 0;
}
