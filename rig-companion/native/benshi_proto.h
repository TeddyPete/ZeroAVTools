// benshi_proto.h -- UV-PRO / VR-N76 / GA-5WB ("Benshi") BLE protocol + AX.25 helpers.
//
// Pure C++ (no Arduino dependencies) so it can be unit-tested on a PC.
// Protocol details come from the open-source benlink (Python) and
// HTCommander (Dart, Apache-2.0) projects.
//
// BLE GATT
//   Service   00001100-d102-11e1-9b23-00025b00a5a5
//   Write     00001101-d102-11e1-9b23-00025b00a5a5  (write with response)
//   Indicate  00001102-d102-11e1-9b23-00025b00a5a5  (radio -> us)
//
// Message on BLE (no GAIA serial wrapper):
//   [group_hi, group_lo, (reply<<7)|cmd_hi, cmd_lo, payload...]
//   group BASIC = 0x0002. An empty payload is sent as a single 0x00 byte.
//
// Packet data (the radio's built-in 1200 baud TNC):
//   HT_SEND_DATA (31) payload = fragment = [hdr][data <= 50 bytes][channel?]
//     hdr bit7 = final fragment, bit6 = channel id byte appended, bits0-5 = fragment id
//   Received data arrives as EVENT_NOTIFICATION (9) with event DATA_RXD (2),
//   the same fragment layout starting at payload[1].
//   The data is a raw AX.25 frame: addresses, control, PID, info. No flags, no FCS.
#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace benshi {

inline constexpr const char* SERVICE_UUID  = "00001100-d102-11e1-9b23-00025b00a5a5";
inline constexpr const char* WRITE_UUID    = "00001101-d102-11e1-9b23-00025b00a5a5";
inline constexpr const char* INDICATE_UUID = "00001102-d102-11e1-9b23-00025b00a5a5";

static const uint16_t GROUP_BASIC = 2;

enum Cmd : uint16_t {
  GET_DEV_INFO = 4,
  REGISTER_NOTIFICATION = 6,
  CANCEL_NOTIFICATION = 7,
  EVENT_NOTIFICATION = 9,
  READ_SETTINGS = 10,
  GET_HT_STATUS = 20,
  HT_SEND_DATA = 31,
};

enum Event : uint8_t {
  EV_HT_STATUS_CHANGED = 1,
  EV_DATA_RXD = 2,
  EV_HT_CH_CHANGED = 5,
  EV_HT_SETTINGS_CHANGED = 6,
  EV_BSS_SETTINGS_CHANGED = 11,
  EV_DATA_TXD = 12,
  EV_POSITION_CHANGED = 13,
  EV_FREQ_MODE_CHANGED = 14,
  EV_FM_CHANGED = 8,
};

enum ReplyStatus : uint8_t {
  SUCCESS = 0, NOT_SUPPORTED = 1, NOT_AUTHENTICATED = 2, INSUFFICIENT_RESOURCES = 3,
  AUTHENTICATING = 4, INVALID_PARAMETER = 5, INCORRECT_STATE = 6, IN_PROGRESS = 7,
};

inline const char* statusName(uint8_t s) {
  static const char* names[] = {"SUCCESS", "NOT_SUPPORTED", "NOT_AUTHENTICATED",
                                "INSUFFICIENT_RESOURCES", "AUTHENTICATING",
                                "INVALID_PARAMETER", "INCORRECT_STATE", "IN_PROGRESS"};
  return s < 8 ? names[s] : "UNKNOWN";
}

static const size_t MAX_FRAGMENT_DATA = 50;  // same limit HTCommander and benlink use

using Bytes = std::vector<uint8_t>;

// ---------------------------------------------------------------- messages
inline Bytes buildCommand(uint16_t cmd, const Bytes& payload = {}) {
  Bytes m = {0x00, (uint8_t)GROUP_BASIC, (uint8_t)((cmd >> 8) & 0x7F), (uint8_t)(cmd & 0xFF)};
  if (payload.empty()) m.push_back(0x00);
  else m.insert(m.end(), payload.begin(), payload.end());
  return m;
}

struct Message {
  bool ok = false;
  uint16_t group = 0;
  bool isReply = false;
  uint16_t cmd = 0;
  Bytes payload;
};

inline Message parseMessage(const uint8_t* d, size_t n) {
  Message m;
  if (n < 4) return m;
  m.group = (uint16_t)((d[0] << 8) | d[1]);
  m.isReply = (d[2] & 0x80) != 0;
  m.cmd = (uint16_t)(((d[2] & 0x7F) << 8) | d[3]);
  m.payload.assign(d + 4, d + n);
  m.ok = true;
  return m;
}

// ---------------------------------------------------------------- fragments
struct Fragment {
  bool final_ = true;
  uint8_t id = 0;
  int channel = -1;  // -1 = not included (radio uses its current channel)
  Bytes data;
};

inline Bytes encodeFragment(const Fragment& f) {
  Bytes out;
  uint8_t hdr = (uint8_t)(f.id & 0x3F);
  if (f.final_) hdr |= 0x80;
  if (f.channel >= 0) hdr |= 0x40;
  out.push_back(hdr);
  out.insert(out.end(), f.data.begin(), f.data.end());
  if (f.channel >= 0) out.push_back((uint8_t)f.channel);
  return out;
}

inline bool decodeFragment(const uint8_t* d, size_t n, Fragment& f) {
  if (n < 1) return false;
  f.final_ = (d[0] & 0x80) != 0;
  bool withCh = (d[0] & 0x40) != 0;
  f.id = d[0] & 0x3F;
  size_t dataLen = n - 1 - (withCh ? 1 : 0);
  if (withCh && n < 2) return false;
  f.data.assign(d + 1, d + 1 + dataLen);
  f.channel = withCh ? d[n - 1] : -1;
  return true;
}

// Split one AX.25 frame into HT_SEND_DATA command messages.
inline std::vector<Bytes> buildSendDataCommands(const Bytes& frame, int channel = -1) {
  std::vector<Bytes> cmds;
  uint8_t id = 0;
  for (size_t i = 0; i < frame.size(); i += MAX_FRAGMENT_DATA, ++id) {
    Fragment f;
    size_t len = frame.size() - i < MAX_FRAGMENT_DATA ? frame.size() - i : MAX_FRAGMENT_DATA;
    f.data.assign(frame.begin() + i, frame.begin() + i + len);
    f.final_ = (i + len) == frame.size();
    f.id = id;
    f.channel = channel;
    cmds.push_back(buildCommand(HT_SEND_DATA, encodeFragment(f)));
  }
  return cmds;
}

// Reassembles DATA_RXD fragments into whole frames (same rules as HTCommander).
class Reassembler {
 public:
  // -1 means untagged, -2 means fragments disagreed on the RX channel.
  int frameChannel()const{return channel_;}
  // Returns true when a complete frame is ready in `out`.
  bool add(const Fragment& f, Bytes& out) {
    if (f.data.size() > 512 || (active_ && buf_.size() + f.data.size() > 512)) {
      active_ = false;
      buf_.clear();
      return false;
    }
    if(f.id==0)channel_=f.channel;
    else if(f.channel>=0 && channel_!=-2)channel_=channel_<0 || channel_==f.channel?f.channel:-2;
    if (!active_) {
      if (f.id != 0) return false;
      buf_ = f.data;
      lastId_ = 0;
      active_ = true;
    } else if (f.id == lastId_ + 1) {
      buf_.insert(buf_.end(), f.data.begin(), f.data.end());
      lastId_ = f.id;
    } else {  // out of sequence: start over from this fragment
      active_ = (f.id == 0);
      buf_ = f.data;
      lastId_ = f.id;
      if (!active_) return false;
    }
    if (f.final_) {
      out = buf_;
      active_ = false;
      buf_.clear();
      return true;
    }
    return false;
  }
 private:
  bool active_ = false;
  int channel_=-1;
  uint8_t lastId_ = 0;
  Bytes buf_;
};

// ---------------------------------------------------------------- HT status
struct HtStatus {
  bool valid = false, powerOn = false, inTx = false, squelchOpen = false, inRx = false;
  int rssi = 0;  // 0..15, 0 = channel quiet
  int currentChannel = -1, region = -1;
  bool fmBroadcast=false;
  int selectedVfo=0;
};

// `p` is the event payload starting at the status bytes (i.e. after the event-type byte).
inline HtStatus parseHtStatus(const uint8_t* p, size_t n) {
  HtStatus s;
  if (n < 2) return s;
  s.powerOn = p[0] & 0x80;
  s.inTx = p[0] & 0x40;
  s.squelchOpen = p[0] & 0x20;
  s.inRx = p[0] & 0x10;
  s.fmBroadcast = p[0] & 1;
  s.selectedVfo=(p[0]>>2)&3;
  s.rssi = n >= 3 ? (p[2] >> 4) : 0;
  s.currentChannel = (p[1] >> 4) | (n >= 4 ? (p[3] & 0x3c) << 2 : 0);
  if (n >= 4) s.region = ((p[2] & 15) << 2) | (p[3] >> 6);
  s.valid = true;
  return s;
}

// ---------------------------------------------------------------- AX.25
struct Address {
  std::string call;
  int ssid = 0;
};

inline bool parseCallsign(const std::string& text, Address& a) {
  std::string call = text;
  a.ssid = 0;
  size_t dash = text.find('-');
  if (dash != std::string::npos) {
    call = text.substr(0, dash);
    std::string s = text.substr(dash + 1);
    if (s.empty() || s.size() > 2) return false;
    for (char c : s) if (c < '0' || c > '9') return false;
    a.ssid = std::stoi(s);
    if (a.ssid > 15) return false;
  }
  if (call.empty() || call.size() > 6) return false;
  for (auto& c : call) {
    if (c >= 'a' && c <= 'z') c = (char)(c - 32);
    if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) return false;
  }
  a.call = call;
  return true;
}

inline void encodeAddress(Bytes& out, const Address& a, bool cBit, bool last) {
  for (int i = 0; i < 6; ++i) {
    char c = i < (int)a.call.size() ? a.call[i] : ' ';
    out.push_back((uint8_t)(c << 1));
  }
  uint8_t b = (uint8_t)(0x60 | ((a.ssid & 0x0F) << 1));
  if (cBit) b |= 0x80;
  if (last) b |= 0x01;
  out.push_back(b);
}

// UI frame (what APRS uses): dest, source, optional digipeaters, 0x03, 0xF0, info.
inline Bytes buildUiFrame(const Address& dest, const Address& src,
                          const std::vector<Address>& path, const std::string& info) {
  Bytes f;
  encodeAddress(f, dest, true, false);
  encodeAddress(f, src, false, path.empty());
  for (size_t i = 0; i < path.size(); ++i) encodeAddress(f, path[i], false, i + 1 == path.size());
  f.push_back(0x03);
  f.push_back(0xF0);
  f.insert(f.end(), info.begin(), info.end());
  return f;
}

struct Decoded {
  bool ok = false;
  std::string dest, src;
  std::vector<std::string> path;
  uint8_t control = 0, pid = 0;
  std::string info;
};

inline std::string addrToString(const uint8_t* d) {
  std::string s;
  for (int i = 0; i < 6; ++i) {
    char c = (char)(d[i] >> 1);
    if (c != ' ') s.push_back(c);
  }
  int ssid = (d[6] >> 1) & 0x0F;
  if (ssid) s += "-" + std::to_string(ssid);
  return s;
}

inline Decoded decodeFrame(const Bytes& f) {
  Decoded r;
  size_t i = 0;
  int n = 0;
  bool last = false;
  while (!last) {
    if (i + 7 > f.size() || n >= 10) return r;
    std::string a = addrToString(&f[i]);
    last = f[i + 6] & 0x01;
    if (n == 0) r.dest = a;
    else if (n == 1) r.src = a;
    else r.path.push_back(a + ((f[i + 6] & 0x80) ? "*" : ""));
    i += 7;
    ++n;
  }
  if (n < 2 || i >= f.size()) return r;
  r.control = f[i++];
  bool hasPid = (r.control & 0x01) == 0 || (r.control & 0xEF) == 0x03;  // I-frame or UI
  if (hasPid) {
    if (i >= f.size()) return r;
    r.pid = f[i++];
  }
  r.info.assign(f.begin() + i, f.end());
  r.ok = true;
  return r;
}

inline std::string toHex(const Bytes& b) {
  static const char* h = "0123456789ABCDEF";
  std::string s;
  for (uint8_t x : b) { s.push_back(h[x >> 4]); s.push_back(h[x & 15]); s.push_back(' '); }
  return s;
}

}  // namespace benshi
