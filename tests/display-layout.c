#include "../host/common/DisplayLayout.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void check(const uint32_t *screens, size_t count, uint32_t built_in,
                  uint32_t previous[ASHACKY_DISPLAY_LIMIT],
                  uint32_t a, uint32_t b, uint32_t c) {
    uint32_t next[ASHACKY_DISPLAY_LIMIT];
    const uint32_t expected[] = {a, b, c};
    ashacky_plan_displays(screens, count, built_in, previous, next);
    assert(memcmp(next, expected, sizeof(next)) == 0);
    memcpy(previous, next, sizeof(next));
}

int main(void) {
    uint32_t bindings[ASHACKY_DISPLAY_LIMIT] = {0};
    const uint32_t all[] = {11, 22, 33}, reordered[] = {33, 11, 22};
    const uint32_t ipad[] = {11, 33}, wired[] = {11, 22}, closed[] = {22, 33};
    const uint32_t excess[] = {44, 22, 33, 11};
    check(all, 1, 11, bindings, 11, 0, 0);
    check(all, 2, 11, bindings, 11, 22, 0);
    check(all, 3, 11, bindings, 11, 22, 33);
    check(reordered, 3, 11, bindings, 11, 22, 33);
    check(ipad, 2, 11, bindings, 11, 0, 33); // Do not move iPad to output 1.
    check(all, 3, 11, bindings, 11, 22, 33);
    check(wired, 2, 11, bindings, 11, 22, 0);
    check(all, 3, 11, bindings, 11, 22, 33);
    check(closed, 2, 11, bindings, 22, 0, 33);
    check(all, 3, 11, bindings, 11, 22, 33);
    check(excess, 4, 11, bindings, 11, 22, 33); // Preserve existing screens at capacity.
    check(NULL, 0, 0, bindings, 0, 0, 0);
    check(ipad, 2, 11, bindings, 11, 33, 0); // iPad connected first.
    check(all, 3, 11, bindings, 11, 33, 22);
    check(wired, 2, 11, bindings, 11, 0, 22);
    assert(ashacky_can_add_sidecar(true, 1, false));
    assert(ashacky_can_add_sidecar(true, 2, false));
    assert(!ashacky_can_add_sidecar(true, 3, false));
    assert(!ashacky_can_add_sidecar(false, 2, false));
    assert(!ashacky_can_add_sidecar(true, 0, false));
    assert(!ashacky_can_add_sidecar(true, 1, true));
    printf("%u\n", ASHACKY_DISPLAY_LIMIT);
    return 0;
}
