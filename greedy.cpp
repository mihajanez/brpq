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
//  $Id: greedy.cpp,v 1.6 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.6 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <vector>

#include "greedy.hpp"

Solution *greedy(const Bay& bay)
{
  Solution *solution = new Solution(bay);
  Bay currentBay = Bay(bay);

  for(int target = 0; target < bay.numberOfBlocks; ++target) {
    currentBay.set_target(target);
    int b;
    while((b = currentBay.relocatable_block()) >= 0) {
      int nonBlockingDestination = -1;
      int nonBlockingMinimumPriority = bay.numberOfBlocks + 1;
      int blockingDestination = -1, blockingSecondaryDestination = -1;
      int blockingMaximumPriority = 0, blockingSecondMaximumPriority = 0;

      for(int d = 0; d < currentBay.numberOfStacks; ++d) {
	if(d == currentBay.targetStack
	   || currentBay.stack[d].height == currentBay.numberOfTiers) {
	  continue;
	}

	if(currentBay.stack[d].minimumPriority > b) {
	  if(nonBlockingMinimumPriority
	     > currentBay.stack[d].minimumPriority) {
	    nonBlockingMinimumPriority = currentBay.stack[d].minimumPriority;
	    nonBlockingDestination = d;
	  }
	} else if(blockingMaximumPriority
		  < currentBay.stack[d].minimumPriority) {
	  blockingSecondMaximumPriority = blockingMaximumPriority;
	  blockingMaximumPriority = currentBay.stack[d].minimumPriority;
	  blockingSecondaryDestination = blockingDestination;
	  blockingDestination = d;
	} else if(blockingSecondMaximumPriority
		  < currentBay.stack[d].minimumPriority) {
	  blockingSecondMaximumPriority = currentBay.stack[d].minimumPriority;
	  blockingSecondaryDestination = d;
	}
      }

      int destination = -1;
      if(nonBlockingDestination >= 0) {
	destination = nonBlockingDestination;
      } else if(blockingDestination == -1) {
	delete solution;
	throw "No destination found";
      } else if(currentBay.stack[blockingDestination].height
		== currentBay.numberOfTiers - 1
		&& blockingSecondaryDestination >= 0) {
	destination = blockingSecondaryDestination;
      } else {
	destination = blockingDestination;
      }

      solution->push(b, currentBay.targetStack, destination);
      currentBay.relocate(destination);
    }

    currentBay.retrieve();
  }

  return solution;
}
