#pragma once
#include "AprsMessage.h"
#include "benshi_proto.h"
#include <algorithm>
#include <cstdint>

namespace aprs {
enum class Receipt { None, Accepted, Rejected };
enum class Authentication { Plain, Verified, Failed, Unchecked };

inline bool isAprsMemory(const benshi::Bytes& record) {
  if (record.size() != 25) return false;
  auto start = record.begin() + 15;
  auto end = std::find(start, record.end(), uint8_t(0));
  while (start != end && *start == ' ') ++start;
  while (start != end && end[-1] == ' ') --end;
  return end-start == 4 && std::equal(start,end,"APRS");
}

inline std::string stationAddress(const std::string& value) {
  benshi::Address address;
  if (!benshi::parseCallsign(value, address)) return {};
  return address.ssid ? address.call + "-" + std::to_string(address.ssid) : address.call;
}

inline bool forStation(const std::string& target, const std::string& local) {
  const auto mine = stationAddress(local);
  const auto destination = stationAddress(target);
  return !mine.empty() && (destination == mine || destination == mine.substr(0, mine.find('-')));
}

struct RadioMessage {
  std::string sender, target, body, id, signature;
  std::string outerSender, relayAuthInput;
  Receipt receipt = Receipt::None;

  std::string signedBody() const {
    return receipt == Receipt::None && !id.empty() ? body + "{" + id : body;
  }
};

inline bool parseRadioMessage(const std::string& from, const std::string& payload, RadioMessage& result) {
  RadioMessage msg;
  msg.sender = stationAddress(from);
  msg.outerSender = msg.sender;
  size_t start = 0;
  if (!payload.empty() && payload.front() == '}') {
    const auto end = payload.find("*:");
    const auto split = payload.find('>');
    if (end == payload.npos || split == payload.npos || split <= 1 || split >= end) return false;
    msg.sender = stationAddress(payload.substr(1, split - 1));
    start = end + 2;
  }
  if (msg.sender.empty() || payload.size() - start < 11 || payload[start] != ':' || payload[start + 10] != ':') return false;
  msg.target = payload.substr(start + 1, 9);
  auto first = msg.target.find_first_not_of(' ');
  if (first == msg.target.npos) return false;
  msg.target = msg.target.substr(first, msg.target.find_last_not_of(' ') - first + 1);
  msg.body = payload.substr(start + 11);
  const auto token = msg.body.rfind('}');
  if (token != msg.body.npos && (msg.body.size() == token + 7 ||
      (msg.body.size() > token + 7 && msg.body[token + 7] == '{'))) {
    msg.signature = msg.body.substr(token + 1, 6);
    msg.body.erase(token, 7);
    // AprsHandler verifies the outer AX.25 information field even when its
    // conversation and ACK target come from a third-party header.
    if (start) {
      auto field = payload.substr(1,9);
      auto begin = field.find_first_not_of(' ');
      field = begin==field.npos ? std::string{} : field.substr(begin,field.find_last_not_of(' ')-begin+1);
      auto remainder = payload.substr(10);
      remainder.erase(start+11+token-10,7);
      msg.relayAuthInput = ":" + msg.outerSender + ":" + field + remainder;
    }
  }
  std::string prefix = msg.body.substr(0, 3);
  for (char& c : prefix) if (c >= 'a' && c <= 'z') c -= 32;
  if (msg.body.size() > 3 && (prefix == "ACK" || prefix == "REJ")) {
    msg.receipt = prefix == "ACK" ? Receipt::Accepted : Receipt::Rejected;
    msg.id = msg.body.substr(3);
    const auto begin = msg.id.find_first_not_of(" \r\n\t");
    msg.id = begin == msg.id.npos ? std::string{} : msg.id.substr(begin, msg.id.find_last_not_of(" \r\n\t") - begin + 1);
  } else {
    const auto seq = msg.body.rfind('{');
    if (seq != msg.body.npos) {
      msg.id = msg.body.substr(seq + 1);
      msg.body.resize(seq);
    }
  }
  result = std::move(msg);
  return true;
}

// Six Base64 characters use the most significant 36 bits of HMAC-SHA256.
inline std::string shortToken(const uint8_t* digest) {
  static constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  uint64_t bits = 0;
  for (int i = 0; i < 5; ++i) bits = (bits << 8) | digest[i];
  std::string token;
  for (int shift = 34; shift >= 4; shift -= 6) token += alphabet[(bits >> shift) & 63];
  return token;
}

inline std::string signatureInput(int64_t minute, const std::string& from, const std::string& to, const std::string& body) {
  return std::to_string(minute) + ":" + from + ":" + to + ":" + body;
}

template<class Sign> Authentication verify(const RadioMessage& msg, const std::string& secret, int64_t minute, Sign sign) {
  if (msg.signature.empty()) return Authentication::Plain;
  if (secret.empty() || minute < 0) return Authentication::Unchecked;
  for (int drift = -2; drift <= 2; ++drift) {
    const auto input = msg.relayAuthInput.empty() ? signatureInput(minute+drift,msg.sender,msg.target,msg.signedBody())
                                                 : std::to_string(minute+drift)+msg.relayAuthInput;
    const auto token = sign(secret,input);
    if (token.empty()) return Authentication::Unchecked;
    if (token == msg.signature) return Authentication::Verified;
  }
  return Authentication::Failed;
}

template<class Sign> std::string messageBody(const std::string& from, const std::string& to, std::string text,
    const std::string& id, const std::string& secret, int64_t minute, Sign sign) {
  const auto suffix = id.empty() ? std::string{} : "{" + id;
  if (!secret.empty()) {
    if (minute < 0) return {};
    const auto token = sign(secret, signatureInput(minute, from, to, text + suffix));
    if (token.size() != 6) return {};
    text += "}" + token;
  }
  return addressed(to, text + suffix);
}

inline bool receiptMatches(const RadioMessage& receipt, Authentication authentication,
    const std::string& peer, const std::string& id, bool signedMessage) {
  const auto expected = signedMessage ? Authentication::Verified : Authentication::Plain;
  return receipt.receipt != Receipt::None && !id.empty() && receipt.id == id &&
         receipt.sender == stationAddress(peer) && authentication == expected;
}
}
