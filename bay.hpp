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
//  $Id: bay.hpp,v 1.7 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.7 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#ifndef _BAY_HPP_
#define _BAY_HPP_
#include <iostream>
#include <vector>

#include "instance.hpp"

class SlotState {
public:
  SlotState() : priority(-1), minimumPriority(-1), stacked(0) { };
  SlotState(const int pr, const int p, const int s)
    : priority(pr), minimumPriority(p), stacked(s) { };
  virtual ~SlotState() { };

  int priority;
  int minimumPriority;
  int stacked;
};

class StackState {
public:
  StackState() : minimumPriority(-1), stacked(0), height(0) { };
  StackState(const int p, const int s, const int h)
    : minimumPriority(p), stacked(s), height(h) { };
  virtual ~StackState() { };

  int minimumPriority;
  int stacked;
  int height;
};

class Bay {
public:
  Bay(const Instance& instance);
  Bay(const Bay& src)
    : targetStack(src.targetStack),
      targetTier(src.targetTier),
      targetPriority(src.targetPriority),
      stack(src.stack), bay(src.bay),
      numberOfStacks(src.numberOfStacks),
      numberOfTiers(src.numberOfTiers),
      numberOfBlocks(src.numberOfBlocks) { };
  virtual ~Bay() { };

  void print(std::ostream& os, const Instance& instance) const;

  void set_target(const int target);
  int remove();
  int retrieve();
  int relocate(const int dst);
  int relocatable_block() const
  {
    if(targetStack == -1 || stack[targetStack].stacked == 0) {
      return -1;
    } else {
      return bay[targetStack].back().priority;
    }
  }

  friend std::ostream& operator<<(std::ostream& os, const Bay& bay);

  int targetStack, targetTier, targetPriority;
  std::vector< StackState > stack;
  std::vector< std::vector< SlotState > > bay;
  int numberOfStacks;
  int numberOfTiers;
  int numberOfBlocks;
};

#endif /* !_BAY_HPP_ */
