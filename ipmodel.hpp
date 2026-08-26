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
//  $Id: ipmodel.hpp,v 1.14 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.14 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#ifndef _IPMODEL_HPP_
#define _IPMODEL_HPP_
#include <iostream>
#include <vector>
#include "gurobi_c++.h"

#include "bay.hpp"
#include "baystate.hpp"
#include "instance.hpp"
#include "parameter.hpp"
#include "sequence.hpp"
#include "solution.hpp"

class IPModel {
public:
  IPModel(const Instance& instance)
    : bayState(instance), sequence(instance.numberOfBlocks),
      conflict(instance.numberOfBlocks),
      lastNumberOfSequences(instance.numberOfBlocks, 0),
      assignmentConstraint(instance.numberOfBlocks),
      capacityConstraint(instance.numberOfBlocks),
      conflictConstraint(instance.numberOfBlocks),
      initialNumberOfVariables(0), initialNumberOfConstraints(0),
      finalNumberOfVariables(0), finalNumberOfConstraints(0),
      lowerBound(-1),
      upperBound(instance.numberOfBlocks*instance.numberOfTiers),
      totalTime(0.0), LBTime(0.0), UBTime(0.0),
      iteration(0), solution(instance.numberOfBlocks, -1),
      solutionUB(instance.numberOfBlocks, -1),
      bestSolution(nullptr) { };
  virtual ~IPModel()
  {
    if(bestSolution != nullptr) {
      delete bestSolution;
    }
  };

  bool solve(const Parameter& parameter);

  int initial_number_of_variables() const
  {
    return initialNumberOfVariables;
  };
  int initial_number_of_constraints() const
  {
    return initialNumberOfConstraints;
  };
  int final_number_of_variables() const
  {
    return finalNumberOfVariables;
  };
  int final_number_of_constraints() const
  {
    return finalNumberOfConstraints;
  };
  double total_time() const { return totalTime; };
  double lb_time() const { return LBTime; };
  double ub_time() const { return UBTime; };
  double total_ip_time() const { return (LBTime + UBTime); };
  int number_of_iterations() const { return iteration; };
  int lower_bound() const { return lowerBound; };
  int upper_bound() const
  {
    if(upperBound == bayState.numberOfBlocks*bayState.numberOfTiers) {
      return -1;
    } else {
      return upperBound;
    }
  };

  Solution* best_solution();

private:
  void add_new_sequence(const int type,
			const int remainingRelocations,
			const Sequence& seq)
  {
    sequence[seq.block.priority]
      .emplace_back(sequence[seq.block.priority].size(),
		    type, remainingRelocations, seq);
  }
  void add_new_sequence(const Sequence& seq)
  {
    sequence[seq.block.priority].emplace_back(seq);
  }
  void update_last_numbers()
  {
    for(const auto& bb1 : bayState.blockingBlock) {
      lastNumberOfSequences[bb1.priority]
	= static_cast<int>(sequence[bb1.priority].size());
    }
  }
  bool is_solution_feasible() const;
  bool is_solution_optimal() const;
  void fix_variables_ub();
  void unfix_variables_ub();
  void expand_solution(const int threshold, const bool verbose = false);
  void expand_sequence(Sequence& srcSequence, const int threshold,
		       const int depth = 1);
  int remaining_relocations(Sequence& srcSequence);
  int earliest_period(const Sequence srcSequence,
		      const int remainingRelocations);
  void add_variables();
  bool check_conflict(const Sequence* sq1, const Sequence* sq2);
  void update_conflict_constraints();
  void update_capacity_constraints();
  void update_lower_bound();
  void add_capacity_constraint(const int period, const int stack,
			       const Sequence& var);
  void add_conflict_constraint(const Sequence& seq1, const Sequence& seq2);

  BayState bayState;
  std::vector< std::vector< Sequence > > sequence;
  std::vector< std::vector< std::vector < std::vector< bool > > > > conflict;
  std::vector< int > lastNumberOfSequences;
  std::vector< GRBConstr > assignmentConstraint;
  std::vector< std::vector< GRBConstr* > > capacityConstraint;
  std::vector< std::vector< std::vector< GRBConstr* > > > conflictConstraint;
  GRBModel* model;
  GRBConstr lowerBoundConstraint;

  int initialNumberOfVariables, initialNumberOfConstraints;
  int finalNumberOfVariables, finalNumberOfConstraints;
  int lowerBound, upperBound;

  double totalTime, LBTime, UBTime;
  int iteration;
  std::vector< int > solution;
  std::vector< int > solutionUB;
  Solution *bestSolution;
};

#endif /* !_IPMODEL_HPP_ */
