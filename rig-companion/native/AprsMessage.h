#pragma once
#include <string>
namespace aprs {
struct Directed {std::string target,text,id;};
inline bool directed(const std::string& data,Directed& m){
  if(data.size()<11 || data[0]!=':' || data[10]!=':')return false;
  m={data.substr(1,9),data.substr(11),""};
  while(!m.target.empty() && m.target.back()==' ')m.target.pop_back();
  if(m.target.empty())return false;
  auto pos=m.text.rfind('{');
  if(pos!=std::string::npos && m.text.size()-pos>=2 && m.text.size()-pos<=6){
    auto candidate=m.text.substr(pos+1);
    if(candidate.find_first_not_of("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")==std::string::npos){
      m.id=candidate;m.text.resize(pos);
    }
  }
  return true;
}
inline std::string addressed(std::string to,const std::string& body){
  to.resize(9,' ');return ":"+to+":"+body;
}
}
