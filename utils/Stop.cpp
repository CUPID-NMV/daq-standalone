#include <csignal>
#include <cstdlib>
#include <unistd.h>

#include "Stop.h"

namespace {

// volatile sig_atomic_t e' l'unico tipo su cui lo standard garantisce che una
// scrittura dal gestore sia visibile al resto del programma.
volatile std::sig_atomic_t gRichiesto = 0;

extern "C" void Gestore(int)
{
    if (gRichiesto) {
        // Secondo segnale: il ciclo non sta rispondendo, probabilmente e'
        // appeso sul link. _exit e' fra le poche cose lecite in un gestore,
        // exit() no perche' chiamerebbe i distruttori.
        const char msg[] = "\nSecondo segnale: esco subito.\n";
        ssize_t n = write(STDERR_FILENO, msg, sizeof(msg) - 1);
        (void)n;
        _exit(130);
    }
    gRichiesto = 1;
}

}  // namespace

namespace Stop {

void Install()
{
    struct sigaction sa;
    sa.sa_handler = Gestore;
    sigemptyset(&sa.sa_mask);
    // Niente SA_RESTART: una chiamata di sistema interrotta deve tornare con
    // EINTR, se no un ciclo fermo in attesa non si accorgerebbe del segnale
    // fino al prossimo evento, che potrebbe non arrivare mai.
    sa.sa_flags = 0;
    sigaction(SIGINT,  &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);
}

bool Requested()
{
    return gRichiesto != 0;
}

}  // namespace Stop
