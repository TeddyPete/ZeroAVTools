#pragma once
#include "AprsRadioMessage.h"
#include "ContactBook.h"
#include <map>
#include <set>
#include <vector>

namespace aprs {
inline std::string phoneDigits(const std::string& address) {
  std::string digits;
  for (char c : address) if (c >= '0' && c <= '9') digits += c;
  return digits;
}

struct ThreadText {
  std::string key, text;
  bool sms = false;
  std::string gatewayAddress;
};

inline ThreadText threadText(const std::string& sender, const std::string& recipient,
                             const std::string& text, bool outgoing, bool addressedToUs) {
  ThreadText result{outgoing ? stationAddress(recipient) : addressedToUs ? stationAddress(sender) : "", text};
  const auto base = sender.substr(0, sender.find('-'));
  const bool gateway = stationAddress(recipient)=="SMS" || (!outgoing && (base=="SMS" || base=="SMSGTE"));
  if (!gateway || text.empty() || text.front() != '@') return result;
  const auto space = text.find(' ');
  if (space == text.npos) return result;
  auto digits = phoneDigits(text.substr(1, space-1));
  if (!digits.empty()){
    auto original=text.substr(1,space-1);
    bool formattedNumber=original.find_first_not_of("0123456789-()+.")==original.npos;
    return {digits, text.substr(space+1), true, formattedNumber?digits:original};
  }
  return result;
}

struct ThreadPreview {
  std::string key, text;
  bool outgoing=false, sms=false;
  std::string gatewayAddress;
  uint64_t activitySeconds=0;
  uint64_t utcSeconds=0;
};
inline std::string activityAge(uint64_t now,uint64_t stamp){
  if(!stamp)return {};
  const auto elapsed=now>stamp?now-stamp:0;
  if(elapsed<60)return "now";
  if(elapsed<3600)return std::to_string(elapsed/60)+"m";
  if(elapsed<86400)return std::to_string(elapsed/3600)+"h";
  if(elapsed<31536000)return std::to_string(elapsed/86400)+"d";
  return std::to_string(elapsed/31536000)+"y";
}

struct ThreadContact {
  std::string name, route;
  bool sms=false;
};

struct MessageRoute {
  benshi::Address destination{"APRS",0};
  std::vector<benshi::Address> hops;
};

inline bool parseMessageRoute(std::string value, MessageRoute& out, bool contactPreference=false) {
  MessageRoute route;
  for(auto& c:value) if(c>='a' && c<='z') c-=32;
  if(value=="STANDARD" || value=="ISS" || value=="NONE"){
    route.destination.call=value=="ISS"?"ARISS":"APN000";
    if(value=="STANDARD")route.hops={{"WIDE1",1},{"WIDE2",2}};
    if(value=="ISS")route.hops={{"WIDE2",1}};
  }else if(!value.empty()){
    // Imported contact records store a profile name. Prior Tab5 records
    // can instead contain explicit SSID paths such as WIDE1-1,WIDE2-2.
    if(contactPreference && value.find_first_of("-,")==value.npos)return false;
    size_t begin=0;
    while(begin<value.size()){
      auto end=value.find(',',begin);
      benshi::Address hop;
      if(route.hops.size()>=8 || !benshi::parseCallsign(contactbook::trim(value.substr(begin,end-begin)),hop))return false;
      route.hops.push_back(hop);
      if(end==value.npos)break;
      begin=end+1;
      if(begin==value.size())return false;
    }
  }
  out=std::move(route);
  return true;
}

class ConversationDirectory {
  std::map<std::string,ThreadContact> contacts;
  std::vector<std::string> alphabetical;
  std::vector<ThreadPreview> active;
  std::set<std::string> activeKeys;
  size_t total=0;

  static std::string folded(std::string text) {
    for(auto& c:text) if(c>='A' && c<='Z') c+=32;
    return text;
  }
public:
  void setContacts(const std::vector<contactbook::Record>& records) {
    contacts.clear();
    alphabetical.clear();
    std::set<std::pair<std::string,std::string>> order;
    for(const auto& r:records) {
      const bool sms=r.fields[3]=="7";
      if(!sms && r.fields[3]!="1") continue;
      const auto address=contactbook::trim(r.fields[0]);
      auto key=sms ? phoneDigits(address) : stationAddress(address);
      if(key.empty()) continue;
      auto& entry=contacts[key];
      if(!r.fields[1].empty()) entry.name=r.fields[1];
      if(!r.fields[4].empty()) entry.route=r.fields[4];
      entry.sms=entry.sms||sms;
    }
    for(const auto& item:contacts) order.emplace(folded(item.second.name.empty()?item.first:item.second.name),item.first);
    for(const auto& item:order) alphabetical.push_back(item.second);
  }
  const ThreadContact* contact(const std::string& key) const {
    auto it=contacts.find(key);
    return it==contacts.end()?nullptr:&it->second;
  }
  void resetRecent() {
    active.clear();
    activeKeys.clear();
    total=contacts.size();
  }
  void addRecent(const ThreadText& thread,bool outgoing,uint64_t activity,uint64_t utc){
    if(thread.key.empty() || !activeKeys.insert(thread.key).second)return;
    active.push_back({thread.key,thread.text,outgoing,thread.sms,thread.gatewayAddress,activity,utc});
    if(!contact(thread.key))++total;
  }
  size_t size() const {return total;}
  std::vector<ThreadPreview> page(size_t offset,size_t limit) const {
    std::vector<ThreadPreview> rows;
    for(const auto& item:active) {
      if(offset){--offset;continue;}
      if(rows.size()>=limit)return rows;
      rows.push_back(item);
    }
    for(const auto& key:alphabetical) {
      if(rows.size()>=limit)break;
      if(activeKeys.count(key))continue;
      if(offset){--offset;continue;}
      rows.push_back({key,{},false,contacts.at(key).sms});
    }
    return rows;
  }
};
}
