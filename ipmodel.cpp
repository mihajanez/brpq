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
//  $Id: ipmodel.cpp,v 1.23 2021/03/15 04:11:58 tanaka Exp tanaka $
//  $Revision: 1.23 $
//  $Date: 2021/03/15 04:11:58 $
//  $Author: tanaka $
//
//
#include <cstdlib>
#include <algorithm>
#include <iostream>
#include <iomanip>
#include <vector>
#include <list>
#include <chrono>
#include "gurobi_c++.h"

#include "greedy.hpp"
#include "ipmodel.hpp"

bool IPModel::solve(const Parameter& parameter)
{
  if(bayState.blockingBlock.size() == 0) {
    lowerBound = upperBound = 0;
    return true;
  }

  GRBEnv* env;
  GRBModel* m;

  try {
    env = new GRBEnv();
    m = new GRBModel(*env);
  } catch(GRBException e) {
    std::cerr << "Error code=" << e.getErrorCode() << std::endl;
    std::cerr << e.getMessage() << std::endl;
    return false;
  }

  model = m;
  if(parameter.verbose < 2) {
    model->set(GRB_IntParam_OutputFlag, 0);
  }
  model->set(GRB_IntParam_Threads, parameter.numberOfThreads);

  auto start = std::chrono::system_clock::now();

  if(parameter.greedyUpperBound) {
    bestSolution = greedy(bayState[0]);
    std::cerr << "greedy_upper_bound=" << bestSolution->number_of_relocations()
	      << std::endl;
  }

  model->setObjective(static_cast<GRBLinExpr>(0.0), GRB_MINIMIZE);

  // assignment constraints
  for(const auto& bb : bayState.blockingBlock) {
    std::ostringstream ss;
    ss << "a(" << bb.priority << ")";
    assignmentConstraint[bb.priority]
      = model->addConstr(static_cast<GRBLinExpr>(0.0), GRB_EQUAL, 1.0,
			 ss.str());
  }

  // capacity constraints
  if(bayState.numberOfTiers < bayState.numberOfBlocks) {
    for(int p = 0; p < bayState.numberOfBlocks - bayState.numberOfTiers - 1;
	++p) {
      capacityConstraint[p].resize(bayState.numberOfStacks, nullptr);
    }
  }

  // conflict constraints
  for(const auto& bb : bayState.blockingBlock) {
    sequence[bb.priority].reserve(100);
    conflict[bb.priority].resize(bb.priority);
    conflictConstraint[bb.priority].resize(bb.priority);
  }

  // lower bound constraint
  lowerBoundConstraint = model->addConstr(0.0, GRB_GREATER_EQUAL,
					  lowerBound, "LB");

  // initial sequences
  for(const auto& bb : bayState.blockingBlock) {
    Sequence seq(bb, Blocking);
    seq.add_relocation(bb.period, bayState[bb.period].targetStack, -2);
    seq.remainingRelocations = 1 + remaining_relocations(seq);
    expand_sequence(seq, parameter.threshold);
  }

#if 0
  for(const auto& bb : bayState.blockingBlock) {
    for(const auto& seq : sequence[bb.priority]) {
      std::cout << seq << std::endl;
    }
  }
#endif

  add_variables();
  update_conflict_constraints();
  if(bayState.numberOfTiers < bayState.numberOfBlocks) {
    update_capacity_constraints();
  }
  update_lower_bound();
  update_last_numbers();
  model->update();

  initialNumberOfVariables = model->get(GRB_IntAttr_NumVars);
  initialNumberOfConstraints = model->get(GRB_IntAttr_NumConstrs);

  bool solved = false;
  for(iteration = 1;; ++iteration) {
    auto current = std::chrono::system_clock::now();
    auto duration = current - start;
    auto msec
      = static_cast<double>(std::chrono::
			    duration_cast<std::chrono::milliseconds>(duration)
			    .count());

    std::cerr << "iteration=" << iteration << ", time=";
    std::cerr << std::fixed << std::setprecision(2) << std::showpoint;
    std::cerr << (0.001*msec) << std::endl;

    if(parameter.timeLimit > 0.0) {
      if(parameter.timeLimit < 0.001*msec) {
	break;
      }
      model->set(GRB_DoubleParam_TimeLimit, parameter.timeLimit - 0.001*msec);
    }

    model->optimize();

    LBTime += model->get(GRB_DoubleAttr_Runtime);

    solution.assign(bayState.numberOfBlocks, -1);

    int optimstatus = model->get(GRB_IntAttr_Status);
    if(optimstatus != GRB_OPTIMAL) {
      break;
    }

    lowerBound = static_cast<int>(model->get(GRB_DoubleAttr_ObjVal) + 0.5);
    std::cerr << "lower_bound=" << lowerBound << std::endl;

    for(const auto& bb : bayState.blockingBlock) {
      for(auto& seq : sequence[bb.priority]) {
	if(seq.type != Inactive
	   && (seq.variable)->get(GRB_DoubleAttr_X) >= 0.5) {
	  solution[bb.priority] = seq.no;
	  if(parameter.verbose > 0) {
	    std::cout << seq << std::endl;
	  }
	  break;
	}
      }
    }

    if(is_solution_optimal() || lowerBound == upperBound
       || (bestSolution != nullptr
	   && lowerBound == bestSolution->number_of_relocations())) {
      if(bestSolution != nullptr) {
	if(upperBound < bestSolution->number_of_relocations()
	   || lowerBound < bestSolution->number_of_relocations()) {
	  delete bestSolution;
	  bestSolution = nullptr;
	} else {
	  upperBound = bestSolution->number_of_relocations();
	}
      }

      if(lowerBound != upperBound) {
	solutionUB = solution;
	upperBound = lowerBound;
      }
      solved = true;
      break;
    }

#if 0
    for(const auto& bb : bayState.blockingBlock) {
      for(const auto& seq : sequence[bb.priority]) {
	std::cout << seq << std::endl;
      }
    }
#endif

    expand_solution(parameter.threshold, (parameter.verbose > 0));
    add_variables();
    update_conflict_constraints();
    if(bayState.numberOfTiers < bayState.numberOfBlocks) {
      update_capacity_constraints();
    }
    update_lower_bound();
    update_last_numbers();

    if(parameter.upperBounding == false) {
      continue;
    }

    fix_variables_ub();

#if 1
    // a trick to detect the infeasibility correctly
    if(upperBound == bayState.numberOfBlocks*bayState.numberOfTiers) {
      model->set(GRB_IntParam_Aggregate, 0);
    }
#endif

    current = std::chrono::system_clock::now();
    duration = current - start;
    msec
      = static_cast<double>(std::chrono::
			    duration_cast<std::chrono::milliseconds>
			    (duration).count());

    if(parameter.timeLimit > 0.0) {
      if(parameter.timeLimit < 0.001*msec) {
	break;
      }
      model->set(GRB_DoubleParam_TimeLimit, parameter.timeLimit - 0.001*msec);
    }

    model->optimize();

    optimstatus = model->get(GRB_IntAttr_Status);

#if 1
    // fail safe
    if(optimstatus == GRB_OPTIMAL
       && upperBound == bayState.numberOfBlocks*bayState.numberOfTiers
       && !is_solution_feasible()) {
      UBTime += model->get(GRB_DoubleAttr_Runtime);
      model->set(GRB_IntParam_Presolve, 0);
      model->reset();
      model->optimize();
      optimstatus = model->get(GRB_IntAttr_Status);
      model->set(GRB_IntParam_Presolve, -1);
    }
#endif

    UBTime += model->get(GRB_DoubleAttr_Runtime);

    if(optimstatus == GRB_OPTIMAL) {
      int ub = static_cast<int>(model->get(GRB_DoubleAttr_ObjVal) + 0.5);
      upperBound = std::min(upperBound, ub);
      std::cerr << "upper_bound=" << ub << std::endl;
      model->set(GRB_IntParam_Aggregate, 1);
      solutionUB.assign(bayState.numberOfBlocks, -1);
      for(const auto& bb : bayState.blockingBlock) {
	for(auto& seq : sequence[bb.priority]) {
	  if(seq.type != Inactive
	     && (seq.variable)->get(GRB_DoubleAttr_X) >= 0.5) {
	    if(parameter.verbose > 0) {
	      std::cout << seq << std::endl;
	    }
	    solutionUB[bb.priority] = seq.no;
	    break;
	  }
	}
      }
      if(lowerBound == upperBound) {
	solved = true;
	if(bestSolution != nullptr) {
	  delete bestSolution;
	  bestSolution = nullptr;
	}
	break;
      }
    } else if(optimstatus == GRB_INFEASIBLE) {
      std::cerr << "upper_bound=infeasible" << std::endl;
    } else {
      break;
    }
    unfix_variables_ub();
#if 1
    model->set(GRB_IntParam_Aggregate, 1);
#endif
  }

  finalNumberOfVariables = model->get(GRB_IntAttr_NumVars);
  finalNumberOfConstraints = model->get(GRB_IntAttr_NumConstrs);

  auto end = std::chrono::system_clock::now();
  auto duration = end - start;
  auto msec
    = static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>
			  (duration).count());

  totalTime += msec*0.001;

  delete m;
  delete env;

  if(!solved && bestSolution != nullptr) {
    if(upperBound < bestSolution->number_of_relocations()) {
      delete bestSolution;
      bestSolution = nullptr;
    } else {
      upperBound = bestSolution->number_of_relocations();
    }
  }

  return solved;
}

Solution *IPModel::best_solution()
{
  if(bestSolution != nullptr) {
    return bestSolution;
  }

  if(upperBound < bayState.numberOfBlocks*bayState.numberOfTiers) {
    std::vector< std::list< Relocation > > s;
    s.resize(bayState.numberOfBlocks);
    for(auto& bb : bayState.blockingBlock) {
      s[bb.priority]
	= sequence[bb.priority][solutionUB[bb.priority]].relocations;
    }
    bestSolution = new Solution(bayState[0], s);
    if(bestSolution->number_of_relocations() != upperBound) {
      delete bestSolution;
      bestSolution = nullptr;
      throw "Error: infeasible solution";
    }
  }

  return bestSolution;
}

#define Tolerance (1.0e-4)

bool IPModel::is_solution_feasible() const
{
  GRBConstr *c = model->getConstrs();

  for(int n = 0; n < model->get(GRB_IntAttr_NumConstrs); ++n) {
    char sense = c[n].get(GRB_CharAttr_Sense);

    if(sense == '>' && c[n].get(GRB_DoubleAttr_Slack) >= Tolerance) {
      return false;
    } else if(sense == '<' && c[n].get(GRB_DoubleAttr_Slack) <= -Tolerance) {
      return false;
    } else if(sense == '='
	      && (c[n].get(GRB_DoubleAttr_Slack) <= -Tolerance
		  || c[n].get(GRB_DoubleAttr_Slack) >= Tolerance)) {
      return false;
    }
  }
  return true;
}

bool IPModel::is_solution_optimal() const
{
  for(const auto& bb : bayState.blockingBlock) {
    if(sequence[bb.priority][solution[bb.priority]].type != NonBlocking) {
      return false;
    }
  }
  return true;
}

void IPModel::fix_variables_ub()
{
  for(const auto& bb : bayState.blockingBlock) {
    for(const auto& seq : sequence[bb.priority]) {
      if(seq.type == Inactive) {
	continue;
      }
      if(seq.type == Blocking) {
	(seq.variable)->set(GRB_DoubleAttr_UB, 0.0);
      } else if(seq.no == solutionUB[bb.priority]) {
	(seq.variable)->set(GRB_DoubleAttr_Start, 1.0);
      } else if(solutionUB[bb.priority] != -1) {
	(seq.variable)->set(GRB_DoubleAttr_Start, 0.0);
      }
    }
  }
}

void IPModel::unfix_variables_ub()
{
  for(const auto& bb : bayState.blockingBlock) {
    for(const auto& seq : sequence[bb.priority]) {
      if(seq.type == Blocking) {
	(seq.variable)->set(GRB_DoubleAttr_UB, 1.0);
      }
    }
  }
}

void IPModel::expand_solution(const int threshold, const bool verbose)
{
  if(verbose) {
    std::cout << "Sequences to be expanded:" << std::endl;
  }

  for(const auto& bb1 : bayState.blockingBlock) {
    const int b1 = bb1.priority;
    if(sequence[b1][solution[b1]].type != NonBlocking
       && sequence[b1][solution[b1]].type != Inactive) {
      Sequence seq(sequence[b1][solution[b1]]);
      if(verbose) {
	std::cout << seq << std::endl;
      }
      expand_sequence(seq, threshold);
      Sequence& origseq = sequence[b1][solution[b1]];
      origseq.type = Inactive;
      model->remove(*(origseq.variable));
      for(const auto& bb2 : bayState.blockingBlock) {
	const int b2 = bb2.priority;
	if(b2 < b1 && conflictConstraint[b1][b2][origseq.no] != nullptr) {
	  model->remove(*(conflictConstraint[b1][b2][origseq.no]));
	  conflictConstraint[b1][b2][origseq.no] = nullptr;
	}
      }
    }
  }
  if(verbose) {
    std::cout << std::endl;
  }
}

void IPModel::expand_sequence(Sequence& srcSequence, const int threshold,
			      const int depth)
{
  const BlockingBlock& bb = srcSequence.block;
  const int period = srcSequence.last_relocation().period;
  const int src = srcSequence.last_relocation().src;
  const int remainingRelocations = srcSequence.remainingRelocations;

  if(remainingRelocations == 1) {
    int nextRelocationBlockingStacks = 0;
    int nextRelocationNonBlockingSlackStacks = 0;

    for(int s = 0; s < bayState.numberOfStacks; ++s) {
      if(s == src
	 || bayState[period].stack[s].height == bayState.numberOfTiers) {
	// source stack or stack is full
	continue;
      }

      srcSequence.change_last_destination(s);

      if(bb.priority < bayState[period].stack[s].minimumPriority) {
	add_new_sequence(NonBlocking, 0, srcSequence);
	Sequence& seq = sequence[bb.priority].back();
	seq.add_relocation(bb.priority, s, -1);
	if(bayState[period].stack[s].height < bayState.numberOfTiers - 1) {
	  ++nextRelocationNonBlockingSlackStacks;
	}
      } else {
	++nextRelocationBlockingStacks;
      }
    }

    srcSequence.change_last_destination(-2);

    if(nextRelocationBlockingStacks > 0
       || (nextRelocationNonBlockingSlackStacks > 0
	   && bayState.precedingBlocks[bb.priority][period].size() > 0)) {
      // at least one blocking sequence remains
      if(depth == 1) {
	add_new_sequence(Blocking, 2, srcSequence);
      } else {
	srcSequence.remainingRelocations = 2;
	expand_sequence(srcSequence, threshold, depth - 1);
	srcSequence.remainingRelocations = 1;
      }
    }

    return;
  }

  for(int s = 0; s < bayState.numberOfStacks; ++s) {
    if(s == src
       || bayState[period].stack[s].height == bayState.numberOfTiers) {
      // source stack or stack is full
      continue;
    }

    std::vector< int > nextPossiblePeriods;

    if(bayState[period].stack[s].height < bayState.numberOfTiers - 1) {
      // previously relocated block may be below this block
      for(auto pr = bayState.precedingBlocks[bb.priority][period].begin();
	  pr != bayState.precedingBlocks[bb.priority][period].end()
	    && *pr < bayState[period].stack[s].minimumPriority; ++pr) {
	nextPossiblePeriods.push_back(*pr);
      }
    }

    if(bayState[period].stack[s].minimumPriority < bb.priority) {
      // always becomes blocking
      nextPossiblePeriods.push_back(bayState[period].stack[s]
				    .minimumPriority);
    }

    srcSequence.change_last_destination(s);

    for(auto p : nextPossiblePeriods) {
      int nextRelocationBlockingStacks = 0;
      int nextRelocationNonBlockingSlackStacks = 0;
      std::vector< int > nextRelocationNonBlockingStacks;

      for(int s2 = 0; s2 < bayState.numberOfStacks; ++s2) {
	if(s2 != s && bayState[p].stack[s2].height < bayState.numberOfTiers) {
	  if(bayState[p].stack[s2].minimumPriority < bb.priority) {
	    ++nextRelocationBlockingStacks;
	  } else {
	    nextRelocationNonBlockingStacks.push_back(s2);
	    if(bayState[p].stack[s2].height < bayState.numberOfTiers - 1) {
	      ++nextRelocationNonBlockingSlackStacks;
	    }
	  }
	}
      }

      srcSequence.add_relocation(p, s, -2);

      if(nextRelocationBlockingStacks > 0
	 || (nextRelocationNonBlockingSlackStacks > 0
	     && bayState.precedingBlocks[bb.priority][p].size() > 0)) {
	if(static_cast<int>(nextRelocationNonBlockingStacks.size())
	   <= threshold) {
	  for(const auto& s2 : nextRelocationNonBlockingStacks) {
	    add_new_sequence(NonBlocking, 0, srcSequence);
	    Sequence& nseq = sequence[bb.priority].back();
	    nseq.change_last_destination(s2);
	    nseq.add_relocation(bb.priority, s2, -1);
	  }

	  if(depth == 1) {
	    if(nextRelocationNonBlockingStacks.empty()) {
	      add_new_sequence(Blocking,
			       1 + remaining_relocations(srcSequence),
			       srcSequence);
	    } else {
	      add_new_sequence(Blocking, 2, srcSequence);
	    }
	  } else {
	    srcSequence.remainingRelocations = 2;
	    expand_sequence(srcSequence, threshold, depth - 1);
	  }
	} else if(depth == 1) {
	  add_new_sequence(Blocking, 1, srcSequence);
	} else {
	  srcSequence.remainingRelocations = 1;
	  expand_sequence(srcSequence, threshold, depth - 1);
	}
      } else {
	// all sequences are nonblocking
	for(const auto& s2 : nextRelocationNonBlockingStacks) {
	  add_new_sequence(NonBlocking, 0, srcSequence);
	  Sequence& nseq = sequence[bb.priority].back();
	  nseq.change_last_destination(s2);
	  nseq.add_relocation(bb.priority, s2, -1);
	}
      }

      srcSequence.remove_relocation();
    }
  }

  srcSequence.remainingRelocations = remainingRelocations;

  return;
}

int IPModel::remaining_relocations(Sequence& srcSequence)
{
  const BlockingBlock& bb = srcSequence.block;
  int period = srcSequence.last_relocation().period, p;
  int src = srcSequence.last_relocation().src, dst;
  int remaining;

  for(remaining = 0;; ++remaining, src = dst, period = p) {
    p = dst = -1;
    for(int s = 0; s < bayState.numberOfStacks; ++s) {
      if(s != src
	 && bayState[period].stack[s].height < bayState.numberOfTiers
	 && bayState[period].stack[s].minimumPriority > p) {
	dst = s;
	p = bayState[period].stack[s].minimumPriority;
      }
    }
    if(p > bb.priority) {
      break;
    }
  }

  return remaining;
}

int IPModel::earliest_period(const Sequence srcSequence,
			     const int remainingRelocations)
{
  if(srcSequence.type == NonBlocking) {
    return 0;
  }

  int t = srcSequence.relocations.back().period;
  int src = srcSequence.relocations.back().src;

  for(int n = remainingRelocations - 1; n > 0; --n) {
    std::vector< int > tmp
      = bayState.precedingBlocks[srcSequence.block.priority][t];
    int minimumPreceding;
    int dst = -1, minimumPriority = srcSequence.block.priority;

    if(tmp.empty()) {
      minimumPreceding = bayState.numberOfBlocks;
    } else {
      minimumPreceding = *std::min_element(tmp.begin(), tmp.end());
    }
    for(int s = 0; s < bayState.numberOfStacks; ++s) {
      if(s != src
	 && bayState[t].stack[s].height < bayState.numberOfTiers
	 && bayState[t].stack[s].minimumPriority < minimumPriority) {
	dst = src;
	minimumPriority = bayState[t].stack[s].minimumPriority;
      }
    }

    if(minimumPreceding < minimumPriority) {
      t = minimumPreceding;
      src = -1;
    } else {
      t = minimumPriority;
      src = dst;
    }
  }

  return t;
}

void IPModel::add_variables() {
  for(const auto& bb : bayState.blockingBlock) {
    for(int sq = lastNumberOfSequences[bb.priority];
	sq < static_cast<int>(sequence[bb.priority].size()); ++sq) {
      std::ostringstream ss;
      ss << "x(" << bb.priority << "," << sq << ")";
      sequence[bb.priority][sq]
	.set_variable(model->addVar(0.0, 1.0,
				    static_cast<double>(sequence[bb.priority]
							[sq].length()),
				    GRB_BINARY, ss.str()));
      model->chgCoeff(assignmentConstraint[bb.priority],
		      *(sequence[bb.priority][sq].variable), 1.0);
      model->chgCoeff(lowerBoundConstraint,
		      *(sequence[bb.priority][sq].variable),
		      static_cast<double>(sequence[bb.priority][sq].length()));
    }
  }
}

bool IPModel::check_conflict(const Sequence* sq1, const Sequence* sq2)
{
  std::list< Relocation >::const_iterator a_itr, b_itr, a_end, b_end;
  const Sequence* a_seq;
  const Sequence* b_seq;

  if(sq1->block.priority < sq2->block.priority) {
    a_seq = sq1;
    b_seq = sq2;
    a_itr = sq1->relocations.begin();
    b_itr = sq2->relocations.begin();
    a_end = sq1->relocations.end();
    b_end = sq2->relocations.end();
  } else {
    a_seq = sq2;
    b_seq = sq1;
    a_itr = sq2->relocations.begin();
    b_itr = sq1->relocations.begin();
    a_end = sq2->relocations.end();
    b_end = sq1->relocations.end();
  }

  if(a_seq->type == NonBlocking && a_seq->block.priority <= b_itr->period) {
    return false;
  }

  int above = 0; // 1: a is above b, -1: b is above a, 0: unrelated
  if(a_itr->src == b_itr->src) {
    if(a_itr->period < b_itr->period) {
      above = 1;
    } else if(a_itr->period > b_itr->period) {
      above = -1;
    } else if(a_seq->block.no < b_seq->block.no) {
      above = 1;
    } else {
      above = -1;
    }
  } else {
    above = 0;
  }

  while(a_itr != a_end && b_itr != b_end) {
    if(a_itr->period < b_itr->period) {
      if(above == -1) {
	// b is above a
	// b should be relocated in the same period
	return true;
      }
      above = (a_itr->dst == b_itr->src)?1:0;
      ++a_itr;
    } else if(b_itr->period < a_itr->period) {
      if(above == 1) {
	// a is above b
	// a should be relocated in the same period
	return true;
      }
      above = (b_itr->dst == a_itr->src)?-1:0;
      ++b_itr;
    } else {
      // a and b are relocated in the same period
      if(above == 0) {
	// above = 0 implies a and b are in different stacks
	return true;
      } else if(a_itr->period == a_seq->block.priority && above != -1) {
	// a is retrieved
	// b is relocated in the same period, implying b is above a
	return true;
      }

      if(a_itr->dst == b_itr->dst) {
	above = -above;
      } else if(a_itr->dst == -2) {
	if(above == -1) {
	  // a is never below b in period t
	  above = 2;
	} else {
	  // arbitrary
	  above = -2;
	}
      } else {
	above = 0;
      }
      ++a_itr;
      ++b_itr;
    }
  }

  if(a_itr == a_end) {
    for(; b_itr != b_end && b_itr->period < a_seq->block.priority; ++b_itr);
    if(b_itr == b_end || b_itr->period != a_seq->block.priority) {
      return false;
    }

    // the sequence of a is truncated and b is relocated in period a
    //     t: the earliest period of the last unfixed relocation of a
    //    t': the earliest period of the first unfixed relocation of a
    //   t'': the period of the relocation of b just before the relocation
    //        in period a
    //     s: the source stack of the relocation of b in period a
    // (1) b is relocated from s' in the same period u as the last fixed
    //     relocation of a
    //   (a) a is above b at the beginning of period u (above=-2)
    //     (i) s'=s  //remReloc=max(2,remReloc)
    //         t''<max(t,t')
    //     (ii) s'!=s //remReloc
    //         t''<t
    //   (b) b is above a at the beginning of period u (above=2)
    //     t''<max(t,u+1) (t''<t or t''==u//remReloc)
    // (2) b is not relocated in the period of the last fixed relocation of a
    //     (above=0)
    //     (i) s'=s  //remReloc=max(2,remReloc)
    //         t''<max(t,t')
    //     (ii) s'!=s //remReloc
    //         t''<t
    --a_itr;
    --b_itr;

    if(above != -2 && b_itr->period <= a_itr->period) {
      return true;
    }

    int n = a_seq->remainingRelocations;
    if(a_itr->src == b_itr->dst) {
      n = std::max(2, n);
    }

    if(b_itr->period < earliest_period(*a_seq, n)) {
      return true;
    }
  }

  return false;
}

void IPModel::update_conflict_constraints()
{
  for(const auto& bb1 : bayState.blockingBlock) {
    const int b1 = bb1.priority;
    for(const auto& bb2 : bayState.blockingBlock) {
      const int b2 = bb2.priority;
      if(b2 < b1) {
	std::vector < std::vector< bool > >& conflictp = conflict[b1][b2];
	std::vector< GRBConstr* >& conflictConstraintp
	  = conflictConstraint[b1][b2];
	conflictp.resize(sequence[b1].size());
	conflictConstraintp.resize(sequence[b1].size(), nullptr);
#if 1
	if(solution[b1] >= 0) {
	  for(int sq = 0; sq < lastNumberOfSequences[b1]; ++sq) {
	    conflictp[sq].resize(sequence[b2].size(), false);
	  }
	  for(int sq = lastNumberOfSequences[b1];
	      sq < static_cast<int>(sequence[b1].size()); ++sq) {
	    conflictp[sq] = conflictp[solution[b1]];
	    conflictp[sq].resize(sequence[b2].size(), false);
	  }
	} else {
	  for(int sq = 0; sq < static_cast<int>(sequence[b1].size()); ++sq) {
	    conflictp[sq].resize(sequence[b2].size(), false);
	  }
	}
#else
	for(int sq = 0; sq < static_cast<int>(sequence[b1].size()); ++sq) {
	  conflictp[sq].resize(sequence[b2].size(), false);
	}
#endif
      }
    }
  }

  for(const auto& bb1 : bayState.blockingBlock) {
    const int b1 = bb1.priority;
    for(const auto& bb2 : bayState.blockingBlock) {
      const int b2 = bb2.priority;
      if(b1 == b2) {
	continue;
      }
      for(int sq1 = lastNumberOfSequences[b1];
	  sq1 < static_cast<int>(sequence[b1].size()); ++sq1) {
	for(int sq2 = 0; sq2 < static_cast<int>(sequence[b2].size()); ++sq2) {
	  if(sequence[b2][sq2].type == Inactive) {
	    continue;
	  }
	  if(b2 < b1) {
	    if(!conflict[b1][b2][sq1][sq2]) {
	      conflict[b1][b2][sq1][sq2]
		= check_conflict(&(sequence[b2][sq2]), &(sequence[b1][sq1]));
	    }
	    if(conflict[b1][b2][sq1][sq2]) {
	      add_conflict_constraint(sequence[b1][sq1], sequence[b2][sq2]);
	    }
	  } else if(!conflict[b2][b1][sq2][sq1]) {
	    if((conflict[b2][b1][sq2][sq1]
		= check_conflict(&(sequence[b1][sq1]), &(sequence[b2][sq2])))) {
	      add_conflict_constraint(sequence[b2][sq2], sequence[b1][sq1]);
	    }
	  }
	}
      }
    }
  }

#if 0
  for(const auto& bb : bayState.blockingBlock) {
    for(const auto& seq : sequence[bb.priority]) {
      std::cout << seq << std::endl;
    }
  }

  for(const auto& bb1 : bayState.blockingBlock) {
    const int b1 = bb1.priority;
    for(const auto& bb2 : bayState.blockingBlock) {
      const int b2 = bb2.priority;
      if(b1 <= b2) {
	continue;
      }

      for(int sq1 = 0; sq1 < static_cast<int>(sequence[b1].size()); ++sq1) {
	if(sequence[b1][sq1].type == Inactive) {
	  continue;
	}
	for(int sq2 = 0; sq2 < static_cast<int>(sequence[b2].size()); ++sq2) {
	  if(sequence[b1][sq1].type == Inactive) {
	    continue;
	  }

	  if(conflict[b1][b2][sq1][sq2]) {
	    std::cout << "(" << b1 << "," << sq1 << ")";
	    std::cout << sequence[b1][sq1] << " : ";
	    std::cout << "(" << b2 << "," << sq2 << ")";
	    std::cout << sequence[b2][sq2] << std::endl;
	  }
	}
      }
    }
  }
#endif
}

void IPModel::update_capacity_constraints()
{
  for(const auto& bb : bayState.blockingBlock) {
    for(int sq = lastNumberOfSequences[bb.priority];
	sq < static_cast<int>(sequence[bb.priority].size()); ++sq) {
      const Sequence& seq = sequence[bb.priority][sq];
      auto itr = seq.relocations.begin();
      int t = itr->period;
      for(++itr; itr != seq.relocations.end(); ++itr) {
	for(; t < itr->period
	      && t < bayState.numberOfBlocks - bayState.numberOfTiers - 1;
	    ++t) {
	  add_capacity_constraint(t, itr->src, sequence[bb.priority][sq]);
	}
      }
    }
  }
}

void IPModel::update_lower_bound()
{
  lowerBoundConstraint.set(GRB_DoubleAttr_RHS, static_cast<double>(lowerBound));
}

void IPModel::add_capacity_constraint(const int period, const int stack,
				      const Sequence& seq)
{
  if(capacityConstraint[period][stack] == nullptr) {
    int slack = bayState.numberOfTiers - bayState[period].stack[stack].height;
    std::ostringstream ss;
    ss << "c(" << period << "," << stack << ")";
    capacityConstraint[period][stack] = new GRBConstr;	
    *(capacityConstraint[period][stack])
      = model->addConstr(*(seq.variable), GRB_LESS_EQUAL,
			 static_cast<double>(slack),
			 ss.str());
  } else {
    model->chgCoeff(*(capacityConstraint[period][stack]),
		    *(seq.variable), 1.0);
  }
}

void IPModel::add_conflict_constraint(const Sequence& seq1,
				      const Sequence& seq2)
{
  int b1 = seq1.block.priority, b2 = seq2.block.priority;

  if(conflictConstraint[b1][b2][seq1.no] == nullptr) {
    std::ostringstream ss;
    ss << "f({" << b1 << "," << seq1.no << "}," << b2 << ")";
    conflictConstraint[b1][b2][seq1.no] = new GRBConstr;
    *(conflictConstraint[b1][b2][seq1.no])
      = model->addConstr(*(seq1.variable) + *(seq2.variable),
			 GRB_LESS_EQUAL, 1.0, ss.str());
  } else {	    
    model->chgCoeff(*(conflictConstraint[b1][b2][seq1.no]),
		    *(seq2.variable), 1.0);
  }
}
