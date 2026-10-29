// Scrive un valore grezzo nel registro della soglia di maggioranza del V812
// (base 0xDDDD0000 + 0x48, A32 user data, D16). Per misurare il peso di un
// canale nella somma, senza passare dalla formula del manuale.
#include <CAENVMElib.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char** argv) {
    if (argc < 2) { puts("uso: majthr VALORE"); return 2; }
    unsigned v = strtoul(argv[1], 0, 0) & 0xFF;
    int32_t h;
    if (CAENVME_Init2(cvETH_V4718, "192.168.99.105", 0, &h) != cvSuccess) { puts("Init fallita"); return 1; }
    uint16_t d = v;
    CVErrorCodes e = CAENVME_WriteCycle(h, 0xDDDD0048, &d, cvA32_U_DATA, cvD16);
    CAENVME_End(h);
    if (e != cvSuccess) { printf("scrittura fallita: %d\n", e); return 1; }
    return 0;
}
