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
//  $Id: solution.hpp,v 1.8 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.8 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#ifndef _SOLUTION_HPP_
#define _SOLUTION_HPP_
#include <iostream>
#include <vector>
#include <list>

#include "bay.hpp"
#include "sequence.hpp"

class SolutionRelocation {
public:
  SolutionRelocation() : priority(-1), src(-1), dst(-1) { };
  SolutionRelocation(const int p, const int s, const int d)
    : priority(p), src(s), dst(d) { };
  virtual ~SolutionRelocation() { };

  int priority;
  int src, dst;
};

class Solution {
public:
  Solution(const Bay& b) : bay(b) { };
  Solution(const Bay& b, std::vector< std::list< Relocation > > sol);

  friend std::ostream& operator<<(std::ostream& os, const Solution& solution);

  void print(std::ostream& os, const Instance& instance) const;
  int number_of_relocations() const
  {
    return static_cast<int>(sequence.size());
  };
  void push(const int priority, const int src, const int dst) {
    sequence.emplace_back(priority, src, dst);
  };
  void pop() { sequence.pop_back(); };

private:
  Bay bay;
  std::vector< SolutionRelocation > sequence;
};

#endif /* _SOLUTION_HPP */
