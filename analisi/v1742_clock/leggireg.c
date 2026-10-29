// Legge registri dei due V1742 (A32, D32) dal bridge V4718. Diagnostica clock.
#include <CAENVMElib.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char** argv) {
    unsigned basi[2] = {0x32100000, 0x11110000};
    unsigned reg[] = {0x8104, 0x8178, 0x8100, 0x811C, 0x8110, 0x8000, 0xF080};
    int32_t h;
    if (CAENVME_Init2(cvETH_V4718, "192.168.99.105", 0, &h) != cvSuccess) { puts("Init fallita"); return 1; }
    for (int b = 0; b < 2; b++) {
        printf("base 0x%08X:", basi[b]);
        for (unsigned i = 0; i < sizeof(reg)/sizeof(reg[0]); i++) {
            uint32_t d = 0;
            CVErrorCodes e = CAENVME_ReadCycle(h, basi[b] + reg[i], &d, cvA32_U_DATA, cvD32);
            if (e == cvSuccess) printf("  %04X=0x%X", reg[i], d); else printf("  %04X=err%d", reg[i], e);
        }
        puts("");
    }
    CAENVME_End(h);
    return 0;
}
