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
//  $Id: sequence.cpp,v 1.11 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.11 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <iostream>
#include <list>
#include <vector>

#include "baystate.hpp"
#include "sequence.hpp"

std::ostream& operator<<(std::ostream& os, const Sequence& sequence)
{
  os << sequence.block.priority+1 << "(" << sequence.no << ")";
  os << "{" << sequence.remainingRelocations << "}:";
  for(const auto& r : sequence.relocations) {
    if(r.dst == -2) {
      if(sequence.remainingRelocations == 1) {
	os << "(" << r.period+1 << ", " << r.src+1 << ", b)";
      } else {
	os << "(" << r.period+1 << ", " << r.src+1 << ", p)";
      }
    } else if(r.dst == -1) {
      os << "(" << r.period+1 << ", " << r.src+1 << ", r)";
    } else {
      os << "(" << r.period+1 << ", " << r.src+1 << ", " << r.dst+1 << ")";
    }
  }

  return os;
}
