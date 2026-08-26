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
//  $Id: sequence.hpp,v 1.8 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.8 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#ifndef _SEQUENCE_HPP_
#define _SEQUENCE_HPP_
#include <iostream>
#include <algorithm>
#include <list>
#include <vector>
#include "gurobi_c++.h"

#include "bay.hpp"
#include "baystate.hpp"

class Relocation
{
public:
  Relocation() : period(-1), src(-1), dst(-1) {};
  Relocation(const int p, const int s, const int d)
      : period(p), src(s), dst(d) {};
  virtual ~Relocation() {};

  int period, src, dst;
};

enum SequenceType
{
  None,
  NonBlocking,
  Blocking,
  Inactive
};

class Sequence
{
public:
  Sequence(const BlockingBlock &bb, const int t)
      : block(bb), no(-1), type(t), remainingRelocations(0),
        relocations(), variable(nullptr), qvariable(nullptr) {};
  Sequence(const BlockingBlock &bb, const int n, const int t, const int nr)
      : block(bb), no(n), type(t), remainingRelocations(nr),
        relocations(), variable(nullptr), qvariable(nullptr) {};
  Sequence(const int n, const int t, const int nr,
           const Sequence &sequence)
      : block(sequence.block), no(n), type(t), remainingRelocations(nr),
        relocations(sequence.relocations)
  {
    if (sequence.variable != nullptr)
    {
      variable = new GRBVar;
      *variable = *(sequence.variable);
    }
    else
    {
      variable = nullptr;
    }
    if (sequence.qvariable != nullptr)
    {
      qvariable = new GRBVar;
      *qvariable = *(sequence.qvariable);
    }
    else
    {
      qvariable = nullptr;
    }
  };
  Sequence(const Sequence &src)
      : block(src.block), no(src.no), type(src.type),
        remainingRelocations(src.remainingRelocations),
        relocations(src.relocations)
  {
    if (src.variable != nullptr)
    {
      variable = new GRBVar;
      *variable = *(src.variable);
    }
    else
    {
      variable = nullptr;
    }
    if (src.qvariable != nullptr)
    {
      qvariable = new GRBVar;
      *qvariable = *(src.qvariable);
    }
    else
    {
      qvariable = nullptr;
    }
  };
  Sequence(Sequence &&src)
      : block(src.block), no(src.no), type(src.type),
        remainingRelocations(src.remainingRelocations),
        relocations(src.relocations)
  {
    variable = src.variable;
    qvariable = src.qvariable;
    src.variable = nullptr;
    src.qvariable = nullptr;
  };
  virtual ~Sequence()
  {
    if (qvariable != nullptr)
    {
      delete qvariable;
    }
    if (variable != nullptr)
    {
      delete variable;
    }
  };

  friend std::ostream &operator<<(std::ostream &os, const Sequence &sequence);

  Sequence &operator=(const Sequence &src) noexcept
  {
    if (this != &src)
    {
      block = src.block;
      no = src.no;
      type = src.type;
      remainingRelocations = src.remainingRelocations;
      relocations = src.relocations;
      if (src.variable != nullptr)
      {
        if (variable == nullptr)
        {
          variable = new GRBVar;
        }
        *variable = *(src.variable);
      }
      if (src.qvariable != nullptr)
      {
        if (qvariable == nullptr)
        {
          qvariable = new GRBVar;
        }
        *qvariable = *(src.qvariable);
      }
    }
    return *this;
  };

  Sequence &operator=(Sequence &&src) noexcept
  {
    if (this != &src)
    {
      block = src.block;
      no = src.no;
      type = src.type;
      remainingRelocations = src.remainingRelocations;
      relocations = src.relocations;
      variable = src.variable;
      qvariable = src.qvariable;
      src.variable = nullptr;
      src.qvariable = nullptr;
    }
    return *this;
  };

  void add_relocation(const int period, const int src, const int dst)
  {
    relocations.emplace_back(period, src, dst);
  };
  void remove_relocation()
  {
    relocations.pop_back();
  };
  void change_last_destination(const int dst)
  {
    relocations.back().dst = dst;
  };
  Relocation &last_relocation()
  {
    return relocations.back();
  };
  int length() const
  {
    return (remainingRelocations + static_cast<int>(relocations.size()) - 1);
  };
  void set_variable(const GRBVar &var)
  {
    if (variable == nullptr)
    {
      variable = new GRBVar;
    }
    *variable = var;
  };
  void set_qvariable(const GRBVar &var)
  {
    if (qvariable == nullptr)
    {
      qvariable = new GRBVar;
    }
    *qvariable = var;
  };
  void delete_variable()
  {
    if (variable != nullptr)
    {
      delete variable;
      variable = nullptr;
    }
    if (qvariable != nullptr)
    {
      delete qvariable;
      qvariable = nullptr;
    }
  };

  BlockingBlock block;
  int no;
  int type;
  int remainingRelocations;
  std::list<Relocation> relocations;
  GRBVar *variable;
  GRBVar *qvariable;
};

#endif /* !_SEQUENCE_HPP_ */
