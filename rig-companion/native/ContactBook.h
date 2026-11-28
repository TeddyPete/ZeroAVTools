#pragma once
#include <array>
#include <string>
#include <vector>
#include <algorithm>
#include <numeric>
#include <set>
namespace contactbook {
// Field order and text format follow upstream models/station_info.dart at 50434c0.
inline constexpr const char* keys[]={"Callsign","Name","Description","StationType","APRSRoute","TerminalProtocol","Channel","ChannelRegion","AX25Destination","AX25Path","AuthPassword","Modem","AvatarIcon","AvatarImage","WaitForConnection"};
inline constexpr const char* labels[]={"Address / callsign","Name","Description","Station type (tap to cycle)","APRS route","Terminal protocol (tap to cycle)","Channel","Channel region","AX.25 destination","AX.25 path","Authentication password","Modem","Avatar icon","Avatar PNG (base64)","Wait for connection"};
inline constexpr const char* typeNames[]={"Generic","APRS","Terminal","BBS","Winlink","Torrent","AGWPE","SMS","Email","MeshCore"};
inline constexpr const char* protocolNames[]={"Raw AX.25","APRS","Compressed AX.25","Connected AX.25"};
struct FileEntry {std::string name;bool directory=false;};
struct Record {
  std::array<std::string,15> fields{};
  Record(){fields[3]="0";fields[5]="3";fields[11]="Hardware";fields[14]="false";}
};
inline std::string trim(std::string s){auto first=s.find_first_not_of(" \t\r\n");if(first==s.npos)return {};return s.substr(first,s.find_last_not_of(" \t\r\n")-first+1);}
inline std::string enumValue(const std::string& s,int maximum,const char* fallback){
  if(s.empty())return fallback;size_t i=(s[0]=='+'||s[0]=='-')?1:0;int n=0;bool negative=s[0]=='-';
  if(i==s.size())return fallback;
  for(;i<s.size();++i){if(s[i]<'0'||s[i]>'9'||n>100)return fallback;n=n*10+s[i]-'0';}
  if(negative)n=-n;return n>=0&&n<=maximum?std::to_string(n):fallback;
}
inline void normalize(Record& r){r.fields[3]=enumValue(r.fields[3],9,"0");r.fields[5]=enumValue(r.fields[5],3,"3");auto s=r.fields[11];for(auto& c:s)if(c>='a'&&c<='z')c-=32;r.fields[11]=s=="AFSK1200"||s=="PSK2400"||s=="DART"?s:"Hardware";}
inline std::vector<Record> parse(const std::string& data,bool internal=false){
  std::vector<Record> result;Record* current=nullptr;size_t pos=0;
  while(pos<data.size()){auto end=data.find_first_of("\r\n",pos);if(end==data.npos)end=data.size();auto raw=data.substr(pos,end-pos);auto line=internal?raw:trim(raw);pos=end+1;
    if(line=="Station:"){result.emplace_back();current=&result.back();continue;}
    if(!current)continue;auto eq=line.find('=');if(eq==line.npos||eq==0)continue;
    auto key=trim(line.substr(0,eq)), value=internal?line.substr(eq+1):trim(line.substr(eq+1));
    for(int i=0;i<(internal?15:14);++i)if(key==keys[i])current->fields[i]=value;
  }
  for(auto& r:result)normalize(r);return result;
}
inline std::string serialize(const std::vector<Record>& records,bool internal=false){
  std::string out;for(const auto& r:records){out+="Station:\n";for(int i=0;i<(internal?15:14);++i)out+=std::string(keys[i])+"="+r.fields[i]+"\n";out+='\n';}return out;
}
inline void merge(std::vector<Record>& records,const std::vector<Record>& imported){
  // Upstream removes all old matching (address,type) records before appending.
  records.erase(std::remove_if(records.begin(),records.end(),[&](const Record& old){return std::any_of(imported.begin(),imported.end(),[&](const Record& r){return old.fields[0]==r.fields[0]&&old.fields[3]==r.fields[3];});}),records.end());
  records.insert(records.end(),imported.begin(),imported.end());
}
inline std::vector<size_t> order(const std::vector<Record>& records,int column,bool ascending){
 std::vector<size_t> indices(records.size());std::iota(indices.begin(),indices.end(),0);
 column=std::max(0,std::min(2,column));
 std::stable_sort(indices.begin(),indices.end(),[&](size_t a,size_t b){return ascending?records[a].fields[column]<records[b].fields[column]:records[b].fields[column]<records[a].fields[column];});return indices;
}
inline bool sameIdentity(const Record& a,const Record& b){return a.fields[0]==b.fields[0]&&a.fields[3]==b.fields[3];}
inline std::vector<Record> withoutSelected(const std::vector<Record>& records,const std::set<size_t>& selected){
 std::vector<Record> out;for(const auto& record:records){bool remove=false;for(auto i:selected)if(i<records.size()&&sameIdentity(record,records[i])){remove=true;break;}if(!remove)out.push_back(record);}return out;
}
inline bool validField(int field,const std::string& value){
  if(value.find_first_of("\r\n")!=value.npos)return false;
  if(field==0)return !trim(value).empty();
  if(field==3)return enumValue(value,9,"invalid")==value;
  if(field==5)return enumValue(value,3,"invalid")==value;
  if(field==11)return value=="Hardware"||value=="AFSK1200"||value=="PSK2400"||value=="DART";
  if(field==14)return value=="true"||value=="false";
  return field>=0&&field<15;
}
}
