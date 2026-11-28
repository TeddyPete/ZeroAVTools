#pragma once
#include <string>
#include <vector>
#include <cstddef>
namespace winmail {
// Literal ASCII protocol behavior of _parseProposalResponses in both pinned
// winlink_client.dart and bbs_handler.dart: numeric suffixes stay with a flag;
// leading digits are ignored. Only exact Y/N dispositions are sent/committed.
inline std::vector<std::string> proposalResponses(const std::string& value,size_t maximum=size_t(-1)){
 std::vector<std::string> result;std::string current;
 for(unsigned char raw:value){
  char c=char(raw);if(c>='a'&&c<='z')c-=32;
  if(c=='+')c='Y';else if(c=='R'||c=='-')c='N';else if(c=='H'||c=='=')c='L';else if(c=='!')c='A';
  if(c>='0'&&c<='9'){if(!current.empty())current+=c;}
  else{if(!current.empty()){result.push_back(std::move(current));if(result.size()>=maximum)return result;}current=std::string(1,c);}
 }
 if(!current.empty())result.push_back(std::move(current));return result;
}
}
