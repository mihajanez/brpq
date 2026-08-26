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
//  $Id: main.cpp,v 1.17 2021/04/09 10:15:05 tanaka Exp tanaka $
//  $Revision: 1.17 $
//  $Date: 2021/04/09 10:15:05 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <exception>
#include <boost/program_options.hpp>

#include "instance.hpp"
#include "parameter.hpp"
#include "solve.hpp"

namespace po = boost::program_options;

int main(const int argc, const char **argv)
{
  int empty_tiers = -1;
  int maximum_height = 0;
  Parameter parameter;

  po::options_description desc("IP-based restricted BRP solver.\nOptions");
  desc.add_options()
    ("help,h", "display help message")
    ("verbose,v", po::value< int >(), "set verbose level")
    ("empty-tiers,E", po::value< int >(), "set number of empty tiers")
    ("maximum-height,T", po::value< int >(), "set maximum number of tiers")
    ("time-limit,t", po::value< double >(), "set time limit")
    ("threads,m", po::value< int >(), "set number of threads")
    ("threshold,s", po::value< int >(), "set threshold for sequence expansion")
    ("disable-greedy,g", "disable initial greedy upper bound")
    ("disable-upper-bound,u", "disable upper bounding")
    ("export-qubo,Q", po::value< std::string >(),
     "export the QUBO formulation to file (for a quantum annealer)")
    ("input-file", po::value< std::string >(), "input file");

  po::positional_options_description pos;
  pos.add("input-file", -1);
  
  po::variables_map vm;

  try {
    po::store(po::command_line_parser(argc, argv)
	      .options(desc).positional(pos).run(), vm);
    po::notify(vm);
  } catch(std::exception &e) {
    std::cerr << std::endl << e.what() << std::endl;
    std::cerr << desc << std::endl;
    return EXIT_FAILURE;
  }

  if(vm.count("help")) {
    std::cout << desc << std::endl;
    return 1;
  }
  if(vm.count("verbose")) {
    parameter.verbose = vm["verbose"].as< int >();
  }
  if(vm.count("empty-tiers")) {
    empty_tiers = vm["empty-tiers"].as< int >();
  }
  if(vm.count("maximum-height")) {
    maximum_height = vm["maximum-height"].as< int >();
  }
  if(vm.count("time-limit")) {
    parameter.timeLimit = vm["time-limit"].as< double >();
  }
  if(vm.count("threads")) {
    parameter.numberOfThreads = vm["threads"].as< int >();
  }
  if(vm.count("threshold")) {
    parameter.threshold = vm["threshold"].as< int >();
  }
  if(vm.count("disable-greedy")) {
    parameter.greedyUpperBound = false;
  }
  if(vm.count("disable-upper-bound")) {
    parameter.upperBounding = false;
  }
  if(vm.count("export-qubo")) {
    parameter.quboOutputFile = vm["export-qubo"].as< std::string >();
  }

  std::istream *pfs = &std::cin;
  std::ifstream ifs;

  if(vm.count("input-file")) {
    ifs.open(vm["input-file"].as< std::string >().c_str(), std::ios::in);
    if(ifs.fail()) {
      std::cerr << "Cannot open file:"
		<< vm["input-file"].as< std::string >().c_str() << std::endl;
      return EXIT_FAILURE;
    } else {
      pfs = &ifs;
    }
  }

  Instance instance;

  try {
    instance.read(*pfs);

    if(pfs != &std::cin) {
      ifs.close();
    }

    if(empty_tiers >= 0) {
      instance.set_empty_tiers(empty_tiers);
    } 
    if(maximum_height > 0) {
      instance.set_maximum_height(maximum_height);
    }
    if(instance.numberOfTiers == 0) {
      instance.numberOfTiers = instance.numberOfBlocks;
    }

    if(parameter.verbose > 0) {
      std::cerr << instance << std::endl;
    }

    solve(instance, parameter);

    return EXIT_SUCCESS;
  } catch(const char *c) {
    std::cerr << c << std::endl;
    return EXIT_FAILURE;
  } catch(...) {
    return EXIT_FAILURE;
  }
}
