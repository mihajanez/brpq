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
//  $Id: parameter.hpp,v 1.9 2021/03/11 05:45:10 tanaka Exp tanaka $
//  $Revision: 1.9 $
//  $Date: 2021/03/11 05:45:10 $
//  $Author: tanaka $
//
//
#ifndef _PARAMETER_HPP_
#define _PARAMETER_HPP_

#include <string>

class Parameter {
public:
  Parameter()
    : verbose(0), timeLimit(-1.0), numberOfThreads(0), threshold(2),
      greedyUpperBound(true), upperBounding(true), quboOutputFile() { };
  ~Parameter() { };

  int verbose;
  double timeLimit;
  int numberOfThreads;
  int threshold;
  bool greedyUpperBound;
  bool upperBounding;
  std::string quboOutputFile;
};

#endif /* !_PARAMETER_HPP_ */
