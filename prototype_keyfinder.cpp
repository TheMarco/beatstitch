// Isolated evaluation helper: mono 44.1 kHz little-endian float32 on stdin.
// Uses libKeyFinder 2.2.8; no application integration or confidence claim.
#include <keyfinder.h>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

int main() {
    try {
#ifdef _WIN32
        _setmode(_fileno(stdin), _O_BINARY);
#endif
        const std::uint16_t endian = 1;
        if (*reinterpret_cast<const unsigned char*>(&endian) != 1)
            throw std::runtime_error("This prototype expects a little-endian host.");
        KeyFinder::KeyFinder finder;
        KeyFinder::Workspace workspace;
        std::vector<float> buffer(44100);
        std::size_t total = 0;
        while (std::cin.read(reinterpret_cast<char*>(buffer.data()), buffer.size() * sizeof(float)) || std::cin.gcount()) {
            auto bytes = std::cin.gcount();
            if (bytes % sizeof(float)) throw std::runtime_error("Incomplete float32 input.");
            auto count = static_cast<std::size_t>(bytes) / sizeof(float);
            total += count;
            if (total > 44100 * 600) throw std::runtime_error("Prototype input exceeds ten minutes.");
            KeyFinder::AudioData block;
            block.setFrameRate(44100);
            block.setChannels(1);
            block.addToSampleCount(count);
            for (std::size_t i = 0; i < count; ++i) {
                if (!std::isfinite(buffer[i])) throw std::runtime_error("Non-finite audio sample.");
                block.setSample(i, buffer[i]);
            }
            finder.progressiveChromagram(block, workspace);
        }
        if (std::cin.bad() || total < 44100) throw std::runtime_error("Input needs at least one second of readable audio.");
        finder.finalChromagram(workspace);
        const int key = finder.keyOfChromagram(workspace);
        const char* names[] = {
            "A major", "A minor", "A# major", "A# minor", "B major", "B minor",
            "C major", "C minor", "C# major", "C# minor", "D major", "D minor",
            "D# major", "D# minor", "E major", "E minor", "F major", "F minor",
            "F# major", "F# minor", "G major", "G minor", "G# major", "G# minor", ""
        };
        if (key < 0 || key > 24) throw std::runtime_error("Unexpected key result.");
        std::cout << "{\"key\":\"" << names[key] << "\",\"frames\":" << total << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
