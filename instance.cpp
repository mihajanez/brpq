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
//  $Id: instance.cpp,v 1.11 2021/04/09 10:15:12 tanaka Exp tanaka $
//  $Revision: 1.11 $
//  $Date: 2021/04/09 10:15:12 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <algorithm>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>
#include <iterator>

#include "instance.hpp"

void Instance::read(std::istream& stream)
{
  enum FileType { None = 0, ExpositoIzquierdo = 1, Caserta = 2, Bacci = 3 };

  std::string str;
  std::vector< int > input_data;
  FileType file_type = None;
  const struct {
    int type;
    std::string key;
  } key_list[] = {
		  {0,  "Tiers"}, {0,  "Height"},
		  {1,  "Width"}, {1,  "Stacks"},
		  {2,  "Containers"},
		  {3,  "Stack "},
		  {-1, ""},
  };

  while(std::getline(stream, str)) {
    int type = -1;

    for(int i = 0; key_list[i].type != -1; ++i) {
      std::size_t pos;

      if((pos = str.find(key_list[i].key)) != std::string::npos) {
	str.erase(pos, key_list[i].key.length());
	type = key_list[i].type;
	break;
      }
    }

    if(type == 3) {
      std::size_t pos;

      if((pos = str.find_first_of(":")) != std::string::npos) {
	int num = stoi(str.substr(0, pos));
	std::istringstream is(str.substr(pos + 1));

	std::copy(std::istream_iterator< int >(is),
		  std::istream_iterator< int >(),
		  std::back_inserter(bay[num]));
      }

      file_type = ExpositoIzquierdo;
    } else if(type != -1) {
      std::size_t pos;

      while((pos = str.find_first_of(" \t:")) != std::string::npos) {
	str.erase(pos, 1);
      }

      int num = stoi(str);

      switch(type) {
      case 0:
	fileNumberOfTiers = num;
	break;
      case 1:
	numberOfStacks = num;
	break;
      case 2:
	break;
      default:
	break;
      }

      file_type = ExpositoIzquierdo;
    }

    if(file_type == ExpositoIzquierdo) {
      continue;
    }

    std::istringstream is(str);

    input_data.clear();
    std::copy(std::istream_iterator< int >(is),
	      std::istream_iterator< int >(),
	      std::back_inserter(input_data));

    if(input_data.size() == 3) {
      file_type = Bacci;
      numberOfStacks = input_data[0];
      fileNumberOfTiers = input_data[1];
      numberOfBlocks = input_data[2];
      break;
    } else if(input_data.size() == 2) {
      file_type = Caserta;
      numberOfStacks = input_data[0];
      numberOfBlocks = input_data[1];
      break;
    }
  }

  if(file_type == Caserta || file_type == Bacci) {
    input_data.clear();
    while(std::getline(stream, str)) {
      std::istringstream is(str);
      std::copy(std::istream_iterator< int >(is),
		std::istream_iterator< int >(),
		std::back_inserter(input_data));
    }

    int s = 0;

    bay.resize(numberOfStacks);
    for(auto itr = input_data.begin(); itr != input_data.end();) {
      int c = *itr;
      if(input_data.end() - itr <= c) {
	throw "File read error: Insufficient block data";
      }

      bay[s++].assign(itr + 1, itr + c + 1);
      itr += c + 1;
    }
  }

  block.reserve(numberOfBlocks);
  for(auto& stack : bay) {
    std::copy(stack.begin(), stack.end(), std::back_inserter(block));
  }

  std::sort(block.begin(), block.end());

  std::vector< int > tmp_array(1 + block.back());
  int i = 0;

  for(const auto b : block) {
    tmp_array[b] = i++;
  }

  for(auto& stack : bay) {
    for(auto& tier : stack) {
      tier = tmp_array[tier];
    }
  }

  if(fileNumberOfTiers > 0) {
    numberOfTiers = fileNumberOfTiers;
  } else {
    for(const auto &stack : bay) {
      fileNumberOfTiers
	= std::max(fileNumberOfTiers, static_cast<int>(stack.size()));
    }
  }
}

void Instance::set_empty_tiers(const int empty_tiers)
{
  numberOfTiers = fileNumberOfTiers + empty_tiers;
  numberOfTiers = std::min(numberOfTiers, numberOfBlocks);
}

void Instance::set_maximum_height(const int maximum_height)
{
  if(numberOfTiers == 0) {
    numberOfTiers = std::min(maximum_height, numberOfBlocks);
  } else {
    numberOfTiers = std::max(maximum_height, numberOfTiers);
  }
  for(const auto &stack : bay) {
    numberOfTiers = std::max(numberOfTiers, static_cast<int>(stack.size()));
  }
}

std::ostream& operator<<(std::ostream& os, const Instance& instance)
{
  os << "stacks=" << instance.numberOfStacks
     << ", tiers=" << instance.numberOfTiers
     << ", blocks=" << instance.numberOfBlocks << std::endl;

  for(int i = std::min(instance.fileNumberOfTiers, instance.numberOfTiers) - 1;
      i >= 0; --i) {
    os << std::setw(3) << (i + 1) << ":";
    for(const auto& stack : instance.bay) {
      if(static_cast<int>(stack.size()) > i) {
	os << "[" << std::setw(3) << instance.block[stack[i]] << "]";
      } else {
	os << "     ";
      }
    }
    os << std::endl;
  }
  os << "   ";
  for(int s = 0; s < instance.numberOfStacks; ++s) {
    os << std::setw(5) << (s + 1);
  }

  return os;
}
