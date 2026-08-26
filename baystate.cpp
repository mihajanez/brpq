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
//  $Id: baystate.cpp,v 1.7 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.7 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <iostream>
#include <vector>
#include <algorithm>

#include "baystate.hpp"

BayState::BayState(const Instance& instance)
  : numberOfStacks(instance.numberOfStacks),
    numberOfTiers(instance.numberOfTiers),
    numberOfBlocks(instance.numberOfBlocks)
{
  bay.emplace_back(instance);

  for(int t = 0; t < numberOfBlocks - 1; ++t) {
    bay.emplace_back(bay.back());

    int pr;
    for(int k = 0; (pr = bay.back().remove()) >= 0; ++k) {
      blockingBlock.emplace_back(pr, t, k, bay.back().targetStack);
    }
    bay.back().retrieve();
    bay.back().set_target(t + 1);
  }
  
  numberOfBlockingBlocks = static_cast<int>(blockingBlock.size());
  periodBBlocks.resize(instance.numberOfBlocks);
  priorityBBlock.assign(instance.numberOfBlocks, nullptr);

  int n = 0;
  for(const auto& bb : blockingBlock) {
    periodBBlocks[bb.period].push_back(&(blockingBlock[n]));
    priorityBBlock[bb.priority] = &(blockingBlock[n++]);
  }

  precedingBlocks.resize(numberOfBlocks);
  for(const auto& bb : blockingBlock) {
    precedingBlocks[bb.priority].resize(bb.priority + 1);
  }

  std::vector< bool > prec(numberOfBlocks, false);

  for(int t = 0; t < numberOfBlocks; ++t) {
    prec[t] = false;
    for(const auto& bb : blockingBlock) {
      if(t >= bb.period and t < bb.priority) {
	for(int t2 = t + 1; t2 < bb.priority; ++t2) {
	  if(prec[t2]) {
	    precedingBlocks[bb.priority][t].push_back(t2);
	  }
	}
      }
    }
    for(auto bb : periodBBlocks[t]) {
      prec[bb->priority] = true;
    }
  }

  for(int t = 0; t < numberOfBlocks; ++t) {
    std::vector< int > samePeriodPrecedingBBlocks;
    for(const auto& pb : periodBBlocks[t]) {
      bool added = false;

      for(auto p : samePeriodPrecedingBBlocks) {
	if(p < pb->priority) {
	  precedingBlocks[pb->priority][pb->period].push_back(p);
	  added = true;
	}
      }

      if(added) {
	std::sort(precedingBlocks[pb->priority][pb->period].begin(),
		  precedingBlocks[pb->priority][pb->period].end());
      }

      samePeriodPrecedingBBlocks.push_back(pb->priority);
    }
  }
}

std::ostream& operator<<(std::ostream& os, const BayState& bayState)
{
  for(int t = 0; t < bayState.numberOfBlocks; ++t) {
    os << "period=" << t << std::endl;
    os << bayState.bay[t] << std::endl << std::endl;
  }

  return os;
}
