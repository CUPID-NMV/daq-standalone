// Riarma il flag di sgancio del PLL (lettura di 0xEF04) e rilegge 0x8104 due volte.
#include <CAENVMElib.h>
#include <stdio.h>
#include <unistd.h>
int main(void) {
    unsigned basi[2] = {0x32100000, 0x11110000};
    int32_t h;
    if (CAENVME_Init2(cvETH_V4718, "192.168.99.105", 0, &h) != cvSuccess) { puts("Init fallita"); return 1; }
    for (int b = 0; b < 2; b++) {
        uint32_t d = 0, s1 = 0, s2 = 0;
        CAENVME_ReadCycle(h, basi[b] + 0xEF04, &d, cvA32_U_DATA, cvD32);
        usleep(200000);
        CAENVME_ReadCycle(h, basi[b] + 0x8104, &s1, cvA32_U_DATA, cvD32);
        usleep(500000);
        CAENVME_ReadCycle(h, basi[b] + 0x8104, &s2, cvA32_U_DATA, cvD32);
        printf("base 0x%08X: EF04=0x%X  8104=0x%X poi 0x%X\n", basi[b], d, s1, s2);
    }
    CAENVME_End(h);
    return 0;
}
