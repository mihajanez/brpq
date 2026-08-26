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
//  $Id: baystate.hpp,v 1.4 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.4 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#ifndef _BAYSTATE_HPP_
#define _BAYSTATE_HPP_
#include <iostream>
#include <vector>

#include "bay.hpp"
#include "instance.hpp"

class BlockingBlock {
public:
  BlockingBlock() : priority(-1), period(-1), no(-1), stack(-1) { };
  BlockingBlock(const int pr, const int p, const int n, const int s)
    : priority(pr), period(p), no(n), stack(s) { };
  virtual ~BlockingBlock() { };

  int priority;
  int period;
  int no;
  int stack;
};

class BayState {
public:
  BayState(const Instance& instance);
  virtual ~BayState() { };

  const Bay& operator [](const int t) const { return bay[t]; };
  friend std::ostream& operator<<(std::ostream& os, const BayState& bayState);

  int numberOfBlockingBlocks;
  int numberOfStacks, numberOfTiers, numberOfBlocks;

  std::vector< Bay > bay;
  std::vector< BlockingBlock > blockingBlock;
  std::vector< std::vector< BlockingBlock* > > periodBBlocks;
  std::vector< BlockingBlock* > priorityBBlock;
  std::vector< std::vector< std::vector< int > > > precedingBlocks;
};

#endif /* !_BAYSTATE_HPP_ */
