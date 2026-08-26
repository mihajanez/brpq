//
// Copyright 2019-2021 Shunji Tanaka and Stefan Voss.  All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions
// are met:
//
//   1. Redistributions of source code must retain the above copyright
//      notice, this list of conditions and the following disclaimer.
//   2. Redistributions in binary form must reproduce the above
//      copyright notice, this list of conditions and the following
//      disclaimer in the documentation and/or other materials
//      provided with the distribution.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDER AND CONTRIBUTORS
// "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
// LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
// FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
// COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
// INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
// (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
// SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
// HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
// STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
// ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
// OF THE POSSIBILITY OF SUCH DAMAGE.
//
//  $Id: instance.hpp,v 1.7 2021/04/09 10:15:10 tanaka Exp tanaka $
//  $Revision: 1.7 $
//  $Date: 2021/04/09 10:15:10 $
//  $Author: tanaka $
//
//
#ifndef _INSTANCE_HPP_
#define _INSTANCE_HPP_
#include <iostream>
#include <vector>

class Instance {
public:
  Instance() : numberOfStacks(0), fileNumberOfTiers(0), numberOfTiers(0),
	       numberOfBlocks(0) { };
  Instance(Instance& src)
    : numberOfStacks(src.numberOfStacks),
      fileNumberOfTiers(src.fileNumberOfTiers),
      numberOfTiers(src.numberOfTiers),
      numberOfBlocks(src.numberOfBlocks)
  {
    block = src.block;
    for(const auto& b : src.bay) {
      bay.push_back(b);
    }
  }
  virtual ~Instance() { };

  void read(std::istream& stream);
  void set_empty_tiers(const int empty_tiers);
  void set_maximum_height(const int maximum_height);

  friend std::ostream& operator<<(std::ostream& os, const Instance& inst);

  std::vector< int > block;
  std::vector< std::vector< int > > bay;
  int numberOfStacks, fileNumberOfTiers, numberOfTiers, numberOfBlocks;
};
#endif /* !_INSTANCE_HPP_ */
