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
//  $Id: bay.cpp,v 1.9 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.9 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <iostream>
#include <iomanip>
#include <vector>

#include "bay.hpp"

Bay::Bay(const Instance& instance)
  : targetStack(-1), targetTier(-1), targetPriority(0),
    bay(instance.numberOfStacks),
    numberOfStacks(instance.numberOfStacks),
    numberOfTiers(instance.numberOfTiers),
    numberOfBlocks(instance.numberOfBlocks)
{
  for(int s = 0; s < numberOfStacks; ++s) {
    int minimumPriority = numberOfBlocks;
    int stacked = 0;
      
    bay[s].emplace_back(-1, minimumPriority, stacked);

    for(auto block : instance.bay[s]) {
      if(block < minimumPriority) {
	minimumPriority = block;
	stacked = 0;
      } else {
	++stacked;
      }

      bay[s].emplace_back(block, minimumPriority, stacked);
    }

    stack.emplace_back(bay[s].back().minimumPriority,
			     bay[s].back().stacked,
			     bay[s].size() - 1);

    if(targetPriority == stack[s].minimumPriority) {
      targetStack = s;
      targetTier = stack[s].height - stack[s].stacked - 1;
    }
  }
}

std::ostream& operator<<(std::ostream& os, const Bay& bay)
{
  int h = 1;

  for(const auto& st : bay.stack) {
    h = std::max(h, st.height);
  }

  for(int t = h; t >= 1; --t) {
    os << std::setw(3) << t << ":";
    for(int s = 0; s < bay.numberOfStacks; ++s) {
      if(static_cast<int>(bay.stack[s].height) >= t) {
	os << "[" << std::setw(3) << bay.bay[s][t].priority << "]";
      } else {
	os << "     ";
      }
    }
    os << std::endl;
  }
  os << "   ";
  for(int s = 0; s < bay.numberOfStacks; ++s) {
    os << std::setw(5) << (s + 1);
  }

  return os;
}

void Bay::print(std::ostream& os, const Instance& instance) const
{
  int h = 1;

  for(const auto& st : stack) {
    h = std::max(h, st.height);
  }

  for(int t = h; t >= 1; --t) {
    os << std::setw(3) << t << ":";
    for(int s = 0; s < numberOfStacks; ++s) {
      if(static_cast<int>(stack[s].height) >= t) {
	os << "[" << std::setw(3) << instance.block[bay[s][t].priority];
	os << "]";
      } else {
	os << "     ";
      }
    }
    os << std::endl;
  }
  os << "   ";
  for(int s = 0; s < numberOfStacks; ++s) {
    os << std::setw(5) << (s + 1);
  }
}

void Bay::set_target(int target)
{
  targetPriority = target;
  targetStack = targetTier = -1;
  for(int s = 0; s < numberOfStacks; ++s) {
    if(targetPriority == stack[s].minimumPriority) {
      targetStack = s;
      targetTier = stack[s].height - stack[s].stacked - 1;
      break;
    }
  }
}

int Bay::remove()
{
  if(targetStack == -1 || stack[targetStack].stacked == 0) {
    return -1;
  }

  int pr = bay[targetStack].back().priority;

  bay[targetStack].pop_back();
  --stack[targetStack].stacked;
  --stack[targetStack].height;
  --numberOfBlocks;

  return pr;
}

int Bay::retrieve()
{
  if(targetStack == -1 || stack[targetStack].stacked > 0) {
    return -1;
  }

  bay[targetStack].pop_back();

  stack[targetStack].stacked = (bay[targetStack].back()).stacked;
  stack[targetStack].minimumPriority
    = (bay[targetStack].back()).minimumPriority;
  --stack[targetStack].height;

  targetStack = targetTier = -1;
  --numberOfBlocks;

  return targetPriority;
}

int Bay::relocate(const int dst)
{
  if(targetStack == -1 || stack[targetStack].stacked == 0) {
    return -1;
  }

  SlotState b = bay[targetStack].back();
  SlotState& bp = bay[dst].back();

  bay[targetStack].pop_back();
  --stack[targetStack].stacked;
  --stack[targetStack].height;

  if(b.priority < bp.minimumPriority) {
    stack[dst].stacked = b.stacked = 0;
    stack[dst].minimumPriority = b.minimumPriority = b.priority;
  } else {
    b.minimumPriority = bp.minimumPriority;
    b.stacked = bp.stacked + 1;
    ++stack[dst].stacked;
  }

  ++stack[dst].height;
  bay[dst].push_back(b);

  return b.priority;
}
