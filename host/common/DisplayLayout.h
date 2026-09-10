#ifndef ASHACKY_DISPLAY_LAYOUT_H
#define ASHACKY_DISPLAY_LAYOUT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Built-in screen, wired monitor and Sidecar. Keep session.py's GPU capacity
 * matched; test_display_layout.py compares the actual QEMU argument to this. */
#define ASHACKY_DISPLAY_LIMIT 3

static inline bool ashacky_screen_present(uint32_t id, const uint32_t *screens, size_t count) {
    if (!id) return false;
    for (size_t i = 0; i < count; ++i) if (screens[i] == id) return true;
    return false;
}

/* Zero is an empty slot, not a real CGDirectDisplayID. Keep surviving external
 * screens on their virtual output even if NSScreen order changes or a lower
 * output disappears. Output zero follows the built-in screen when available. */
static inline void ashacky_plan_displays(const uint32_t *screens, size_t count,
                                         uint32_t built_in,
                                         const uint32_t previous[ASHACKY_DISPLAY_LIMIT],
                                         uint32_t next[ASHACKY_DISPLAY_LIMIT]) {
    for (size_t i = 0; i < ASHACKY_DISPLAY_LIMIT; ++i) next[i] = 0;
    if (!count) return;
    if (ashacky_screen_present(built_in, screens, count)) next[0] = built_in;
    else if (ashacky_screen_present(previous[0], screens, count)) next[0] = previous[0];
    else next[0] = screens[0];
    for (size_t i = 1; i < ASHACKY_DISPLAY_LIMIT; ++i) {
        if (ashacky_screen_present(previous[i], screens, count) &&
            !ashacky_screen_present(previous[i], next, ASHACKY_DISPLAY_LIMIT)) next[i] = previous[i];
    }
    for (size_t i = 0; i < count; ++i) {
        if (!screens[i] || ashacky_screen_present(screens[i], next, ASHACKY_DISPLAY_LIMIT)) continue;
        for (size_t slot = 1; slot < ASHACKY_DISPLAY_LIMIT; ++slot) {
            if (!next[slot]) { next[slot] = screens[i]; break; }
        }
    }
}

/* macOS remains responsible for supported physical/Sidecar combinations.
 * Reserve a virtual output before asking it to connect another screen. */
static inline bool ashacky_can_add_sidecar(bool count_known, uint32_t total,
                                           bool sidecar_connected) {
    return count_known && total > 0 && total < ASHACKY_DISPLAY_LIMIT && !sidecar_connected;
}

#endif
