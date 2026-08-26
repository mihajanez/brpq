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
//  $Id: solution.cpp,v 1.10 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.10 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <iostream>
#include <iomanip>
#include <vector>
#include <list>

#include "solution.hpp"

Solution::Solution(const Bay& b, std::vector< std::list< Relocation > > sol)
  : bay(b)
{
  Bay current_bay(bay);
  auto itr = new std::list< Relocation >::iterator[sol.size()];

  for(int i = 0; i < static_cast<int>(sol.size()); ++i) {
    itr[i] = sol[i].begin();
  }

  for(int target = 0; target < bay.numberOfBlocks; ++target) {
    current_bay.set_target(target);

    int b;

    while((b = current_bay.relocatable_block()) >= 0) {
      if(itr[b] == sol[b].end()
	 || itr[b]->period != target
	 || itr[b]->src != current_bay.targetStack) {
	delete[] itr;
	sequence.erase(sequence.begin(), sequence.end());
	std::cerr << "Infeasible solution" << std::endl;
	return;
      }

      current_bay.relocate(itr[b]->dst);
      sequence.emplace_back(b, current_bay.targetStack, itr[b]->dst);
      ++itr[b];
    }

    current_bay.retrieve();
  }

  delete[] itr;

  if(bay.numberOfBlocks == 0) {
    sequence.erase(sequence.begin(), sequence.end());
  }
}

std::ostream& operator<<(std::ostream& os, const Solution& solution)
{
  os << "--------" << std::endl;

#if 0
  for(const auto& s : solution.sequence) {
    os << "(" << s.priority << "," << s.src << "," << s.dst << ")";
  }
  os << std::endl;
#endif

  Bay current_bay(solution.bay);

  os << "Initial configuration" << std::endl;
  os << current_bay << std::endl;

  int i = 0;
  int target = 0;

  current_bay.set_target(target);
  while(current_bay.numberOfBlocks > 0) {
    int c = 0;

    for(c = 0;
	current_bay.relocatable_block() < 0 && current_bay.numberOfBlocks > 0;
	++c) {
      current_bay.retrieve();
      current_bay.set_target(++target);
    }

    if(c > 0) {
      os << "++++++++" << std::endl;
      os << "Retrieve " << c << " block";
      if(c > 1) {
	os << "s";
      }
      os << std::endl;

      if(current_bay.numberOfBlocks == 0) {
	os << "--------" << std::endl;
	break;
      }
      os << current_bay << std::endl;
    }

    int b;

    while((b = current_bay.relocatable_block()) >= 0) {
      if(b != solution.sequence[i].priority
	 || current_bay.targetStack != solution.sequence[i].src) {
	exit(1);
      }

      os << "--------" << std::endl;
      os << "Relocation " << (i + 1) << ": [";
      os << std::setw(3) << solution.sequence[i].priority << "] ";
      os << (solution.sequence[i].src + 1) << "->";
      os << (solution.sequence[i].dst + 1);
      os << std::endl;

      current_bay.relocate(solution.sequence[i++].dst);
      os << current_bay << std::endl;
    }
  }

  return os;
}

void Solution::print(std::ostream& os, const Instance& instance) const
{
  os << "--------" << std::endl;

#if 0
  for(const auto& s : solution.sequence) {
    os << "(" << instance.block[s.priority] << ",";
    os << (s.src + 1) << "," << (s.dst + 1) << ")";
  }
  os << std::endl;
#endif

  Bay current_bay(bay);

  os << "Initial configuration" << std::endl;
  current_bay.print(os, instance);
  os << std::endl;

  int i = 0;
  int target = 0;

  current_bay.set_target(target);
  while(current_bay.numberOfBlocks > 0) {
    int c = 0;

    for(c = 0;
	current_bay.relocatable_block() < 0 && current_bay.numberOfBlocks > 0;
	++c) {
      current_bay.retrieve();
      current_bay.set_target(++target);
    }

    if(c > 0) {
      os << "++++++++" << std::endl;
      os << "Retrieve " << c << " block";
      if(c > 1) {
	os << "s";
      }
      os << std::endl;

      if(current_bay.numberOfBlocks == 0) {
	os << "--------" << std::endl;
	break;
      }
      current_bay.print(os, instance);
      os << std::endl;
    }

    int b;

    while((b = current_bay.relocatable_block()) >= 0) {
      if(b != sequence[i].priority
	 || current_bay.targetStack != sequence[i].src) {
	exit(1);
      }

      os << "--------" << std::endl;
      os << "Relocation " << (i + 1) << ": [";
      os << std::setw(3) << instance.block[sequence[i].priority] << "] ";
      os << (sequence[i].src + 1) << "->";
      os << (sequence[i].dst + 1);
      os << std::endl;

      current_bay.relocate(sequence[i++].dst);
      current_bay.print(os, instance);
      os << std::endl;
    }
  }
}
