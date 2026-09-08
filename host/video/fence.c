#include <stdatomic.h>
void lh_release(void){atomic_thread_fence(memory_order_release);}
