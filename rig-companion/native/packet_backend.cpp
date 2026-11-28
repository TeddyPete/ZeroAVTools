#include <algorithm>
#include "PacketLink.h"
#include "RigWinlinkSession.h"
#include "YappTransfer.h"
#include <filesystem>
#include <fstream>
#include <set>
#include <unistd.h>
#include <fcntl.h>

namespace fs = std::filesystem;
using Emit = void (*)(void*, int, const char*, size_t);
using Digest = int (*)(const uint8_t*, size_t, uint8_t*);

struct MailFiles {
    fs::path root;
    fs::path path(const std::string& name) const { return root / name.substr(1); }
    bool exists(const std::string& n) { return fs::exists(path(n)); }
    bool mkdir(const std::string& n) { fs::create_directories(path(n)); return fs::is_directory(path(n)); }
    bool remove(const std::string& n) { return fs::remove(path(n)); }
    bool rename(const std::string& from, const std::string& to) {
        fs::rename(path(from), path(to)); return true;
    }
    int64_t size(const std::string& n) {
        std::error_code ec; auto length = fs::file_size(path(n), ec);
        return ec ? -1 : static_cast<int64_t>(length);
    }
    std::string range(const std::string& n, size_t offset, size_t count) {
        std::ifstream file(path(n), std::ios::binary);
        file.seekg(offset);
        std::string data(count, '\0'); file.read(data.data(), count); data.resize(file.gcount());
        return data;
    }
    static bool writeFile(const fs::path& path, const std::string& data, int flags) {
        int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_NOFOLLOW | flags, 0600);
        if (fd < 0) return false;
        size_t sent = 0;
        while (sent < data.size()) {
            auto n = ::write(fd, data.data() + sent, data.size() - sent);
            if (n <= 0) { ::close(fd); return false; }
            sent += static_cast<size_t>(n);
        }
        bool ok = fsync(fd) == 0;
        return ::close(fd) == 0 && ok;
    }
    bool write(const std::string& n, const std::string& data) { return writeFile(path(n), data, O_TRUNC); }
    bool append(const std::string& n, const std::string& data) { return writeFile(path(n), data, O_APPEND); }
    void yieldIo() {}
    std::vector<std::string> ids() {
        std::set<std::string> found;
        auto folder = path("/rig-companion/winlink/mail");
        if (!fs::is_directory(folder)) return {};
        for (const auto& item : fs::directory_iterator(folder)) {
            auto name = item.path().filename().string();
            auto sep = name.find(".mail");
            if (!item.is_regular_file() || sep == name.npos || !sep || sep > 128 || sep % 2
                || name.find_first_not_of("0123456789abcdef", 0) != sep) continue;
            auto suffix = name.substr(sep);
            if (suffix != ".mail" && suffix != ".mail.a" && suffix != ".mail.b") continue;
            std::string mid;
            for (size_t i = 0; i < sep; i += 2) mid += static_cast<char>(std::stoi(name.substr(i, 2), nullptr, 16));
            if (winmail::validMid(mid)) found.insert(mid);
        }
        return {found.begin(), found.end()};
    }
};

struct Link {
    packetlink::Session ax;
    companion::rigwinlink::Session wl;
    MailFiles files;
    yapp::Transfer transfer;
    bool mail = false, announced = false, acceptFiles = false, finish = false;
    uint32_t began = 0, activity = 0, last = 0;
    Emit emit; void* user;
    fs::path directory, partial;
    Link(const char* base, const char* received, Emit handler, void* ctx)
        : files{base}, emit(handler), user(ctx), directory(received) { transfer.spoolReceive = true; }
    ~Link() { if (!partial.empty()) { std::error_code error; fs::remove(partial, error); } }
    void event(int kind, const std::string& value) { emit(user, kind, value.data(), value.size()); }
    void frame(const packetlink::Bytes& value) {
        if (!value.empty()) emit(user, 0, reinterpret_cast<const char*>(value.data()), value.size());
    }
    int state() const { return static_cast<int>(ax.state); }
    void transferBytes(uint32_t now) {
        if (transfer.sinkPending()) {
            using S = yapp::State;
            auto id = transfer.sinkId();
            bool ok = false;
            if (transfer.state == S::OpenReceive && transfer.size >= 0 && transfer.size <= 2*1024*1024) {
                fs::create_directories(directory);
                std::string pattern = (directory / "received-XXXXXX").string();
                int fd = mkstemp(pattern.data());
                if (fd >= 0) { ::close(fd); partial = pattern; ok = true; }
            } else if (transfer.state == S::WriteReceive && !partial.empty()
                       && transfer.transferred + transfer.sinkData().size() <= 2*1024*1024) {
                ok = MailFiles::writeFile(partial, transfer.sinkData(), O_APPEND);
            } else if (transfer.state == S::FinishReceive && !partial.empty()) {
                std::string name = yapp::suggestedSavePath(transfer.filename).substr(1);
                auto target = directory / (partial.filename().string() + "-" + name);
                ok = ::link(partial.c_str(), target.c_str()) == 0;
                if (ok) { fs::remove(partial); event(2, "Received " + target.filename().string()); }
            }
            transfer.supplySink(id, ok, partial.string(), now);
            if (transfer.state == S::Header && ok) partial.clear();
            transfer.completed.clear();
        }
        while (!transfer.tx.empty() && ax.queueData(transfer.tx.front())) transfer.tx.pop_front();
        if (!transfer.error.empty()) { event(2, transfer.error); transfer.error.clear(); }
        if (!transfer.sentFile.empty()) { event(2, "Sent " + transfer.sentFile); transfer.sentFile.clear(); }
    }
    void pumpMail(uint32_t now) {
        using S = winmail::Client::State;
        if (!announced && ax.state == packetlink::State::Online) { wl.client.connected(); announced = true; activity = now; }
        if (announced && ax.state == packetlink::State::Online && now - activity > 90000)
            wl.client.abort("Winlink gateway timed out");
        std::vector<std::string> outbox;
        if (wl.client.exchange.phase == winmail::Receive::Phase::Prepare) {
            for (auto& id: files.ids()) {
                winmail::Managed entry; std::string error;
                if (!winmail::loadManaged(files, id, entry, error)) { wl.client.abort(error); break; }
                if (entry.box == 'O') outbox.push_back(id);
            }
        }
        wl.storageStep(files, outbox);
        if (auto data = wl.client.control(); data && ax.queueData(*data)) wl.client.controlSent();
        if (wl.client.state == S::Failed && ax.state != packetlink::State::Closing) frame(ax.close(now));
        if (wl.client.readyToClose() && !ax.queuedBytes && ax.outstanding.empty()
            && ax.state == packetlink::State::Online) frame(ax.close(now));
    }
    void tick(uint32_t now) {
        last = now;
        if (mail) pumpMail(now);
        else { transfer.tick(now); transferBytes(now); }
        frame(ax.retry(now));
        if (!ax.notice.empty()) { event(2, ax.notice); ax.notice.clear(); }
        if (ax.state == packetlink::State::Offline && !finish) {
            finish = true;
            if (mail) { wl.client.disconnected(); event(2, "Mail: " + std::to_string(wl.saved)
                        + " received, " + std::to_string(wl.sent) + " sent"); }
        }
    }
    void receive(const uint8_t* data, size_t n, uint32_t now) {
        auto reply = ax.accept({data, data+n}, ax.local, false, now); frame(reply);
        if (mail && ax.state == packetlink::State::Online && !announced) {
            wl.client.connected(); announced = true; activity = now;
        }
        for (auto& packet: ax.receivedPackets) {
            if (mail) { activity = now; wl.client.feed(packet); }
            else if (!(acceptFiles || transfer.busy()) || !transfer.accept(packet, now)) event(1, packet);
        }
        ax.receivedPackets.clear();
        tick(now);
    }
};

extern "C" {
void* rc_new(const char* root, const char* files, Emit output, void* user) {
    try { return new Link(root, files, output, user); } catch (...) { return nullptr; }
}
void rc_free(Link* h) { delete h; }
int rc_state(Link* h) { return h->state(); }
int rc_open(Link* h, const char* local, const char* peer, const char* password,
            int mail, int extended, uint32_t now, Digest digest) {
    try {
        h->mail = mail != 0; h->began = h->activity = now;
        if (h->mail) {
            winmail::ClientConfig config; config.transport = winmail::ClientConfig::Transport::Radio;
            config.callsign = local; config.password = password;
            if (!h->wl.begin(std::move(config), [digest](const auto& bytes, auto& out) {
                return digest(bytes.data(), bytes.size(), out.data()) != 0;
            })) return 0;
            h->wl.client.traffic = [h](const std::string& info, char role) { if (!role) h->event(2, info); };
        }
        auto frame = h->ax.open(local, peer, now, {}, extended != 0);
        if (frame.empty()) return 0;
        h->frame(frame); return 1;
    } catch (...) { return 0; }
}
int rc_tick(Link* h, uint32_t now) { try { h->tick(now); return 1; } catch (...) { return 0; } }
int rc_rx(Link* h, const uint8_t* data, size_t n, uint32_t now) {
    try { h->receive(data, n, now); return 1; } catch (...) { return 0; }
}
int rc_send(Link* h, const char* data, size_t n) {
    try { return !h->mail && !h->transfer.busy() && h->ax.queueData({data,n}); } catch (...) { return 0; }
}
int rc_yapp(Link* h, const char* name, const char* data, size_t n, uint32_t now) {
    try {
        return !h->mail && h->state() == 2 && n <= 2*1024*1024 && h->transfer.send(name, {data,n}, now);
    } catch (...) { return 0; }
}
void rc_accept_yapp(Link* h, int value) { h->acceptFiles = value != 0; }
int rc_close(Link* h, uint32_t now) {
    try {
        if (h->mail) h->wl.client.cancel();
        h->frame(h->ax.close(now)); return 1;
    } catch (...) { return 0; }
}
int rc_save_mail(Link* h, const char* id, const char* date, const char* call, const char* to,
                 const char* subject, const char* body, int queue) {
    try {
        winmail::Draft draft; draft.mid=id; draft.date=date; draft.from=call; draft.subject=subject; draft.body=body;
        std::string error; winmail::Managed previous;
        if (!winmail::cleanRecipients(to, draft.to) || draft.to.empty()) return 0;
        auto path = winmail::mailPath(id);
        if (path.empty()) return 0;
        if (h->files.exists(path) || h->files.exists(path+".a") || h->files.exists(path+".b")) {
            if (!winmail::loadManaged(h->files, id, previous, error)
                || (previous.box != 'D' && previous.box != 'O')) return 0;
            draft = winmail::editMail(previous.mail);
            draft.generation = previous.generation;
            draft.date = date; draft.from = call;
            if (!winmail::cleanRecipients(to, draft.to) || draft.to.empty()) return 0;
            draft.subject = subject; draft.body = body;
        }
        return winmail::saveManaged(h->files, draft, queue ? 'O' : 'D', error);
    } catch (...) { return 0; }
}
int rc_list_mail(Link* h, const char* id) {
    try {
        auto ids = *id ? std::vector<std::string>{id} : h->files.ids();
        for (const auto& key: ids) {
            winmail::Managed mail; std::string error;
            if (!winmail::loadManaged(h->files, key, mail, error)) continue;
            auto& m=mail.mail.message;
            std::string value(1, mail.box);
            for (const auto* field: {&m.mid, &m.date, &m.from, &m.to, &m.subject}) { value += '\0'; value += *field; }
            if (*id) { value += '\0'; value.append(mail.mail.raw.begin()+m.body.offset, mail.mail.raw.begin()+m.body.offset+m.body.size); }
            h->event(3, value);
        }
        return 1;
    } catch (...) { return 0; }
}
}
