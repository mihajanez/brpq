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
#ifndef _QUBOMODEL_HPP_
#define _QUBOMODEL_HPP_
#include <map>
#include <string>
#include <utility>
#include <vector>
#include "gurobi_c++.h"

//#include "bay.hpp"
#include "baystate.hpp"
#include "instance.hpp"
#include "parameter.hpp"
#include "sequence.hpp"
#include "solution.hpp"

class QUBOModel
{
public:
  QUBOModel(const Instance &instance)
      : bayState(instance), sequence(instance.numberOfBlocks),
        conflict(instance.numberOfBlocks),
        lastNumberOfSequences(instance.numberOfBlocks, 0),
        qObjective(instance.numberOfBlocks),
        assignmentConstraint(instance.numberOfBlocks),
        qAssignmentConstraint(instance.numberOfBlocks),
        capacityConstraint(instance.numberOfBlocks),
        capacitySlackVars(instance.numberOfBlocks),
        conflictConstraint(instance.numberOfBlocks),
        qConflictConstraint(instance.numberOfBlocks),
        quboCaptured(false),
        initialNumberOfVariables(0), initialNumberOfConstraints(0),
        finalNumberOfVariables(0), finalNumberOfConstraints(0),
        lowerBound(-1),
        upperBound(instance.numberOfBlocks * instance.numberOfTiers),
        totalTime(0.0), LBTime(0.0), UBTime(0.0),
        iteration(0), solution(instance.numberOfBlocks, -1),
        solutionUB(instance.numberOfBlocks, -1),
        bestSolution(nullptr) {};
  virtual ~QUBOModel()
  {
    if (bestSolution != nullptr)
    {
      delete bestSolution;
    }
  };

  bool solve(const Parameter &parameter);

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
    if (upperBound == bayState.numberOfBlocks * bayState.numberOfTiers)
    {
      return -1;
    }
    else
    {
      return upperBound;
    }
  };

  Solution *best_solution();

  bool export_qubo(const std::string &filename) const;

  // Prints the IP variables selected in the optimal solution of the IP
  // model that was solved to build the exported QUBO (i.e. the x(p,sq)
  // variables also captured by capture_qubo()/export_qubo()), together
  // with each selected variable's cost and the total cost of the
  // solution. Must be called after solve() and only when upper_bound()
  // is not -1 (i.e. a feasible solution was found).
  void print_solution(std::ostream &os) const;

private:
  void add_new_sequence(const int type,
                        const int remainingRelocations,
                        const Sequence &seq)
  {
    sequence[seq.block.priority]
        .emplace_back(sequence[seq.block.priority].size(),
                      type, remainingRelocations, seq);
  }
  void add_new_sequence(const Sequence &seq)
  {
    sequence[seq.block.priority].emplace_back(seq);
  }
  void update_last_numbers()
  {
    for (const auto &bb1 : bayState.blockingBlock)
    {
      lastNumberOfSequences[bb1.priority] = static_cast<int>(sequence[bb1.priority].size());
    }
  }
  bool is_solution_feasible() const;
  bool is_solution_optimal() const;
  void fix_variables_ub();
  void unfix_variables_ub();
  void expand_solution(const int threshold, const bool verbose = false);
  void expand_sequence(Sequence &srcSequence, const int threshold,
                       const int depth = 1);
  int remaining_relocations(Sequence &srcSequence);
  int earliest_period(const Sequence srcSequence,
                      const int remainingRelocations);
  void add_variables();
  bool check_conflict(const Sequence *sq1, const Sequence *sq2);
  void update_conflict_constraints();
  void update_capacity_constraints();
  void update_lower_bound();
  void add_capacity_constraint(const int period, const int stack,
                               const Sequence &var);
  void add_conflict_constraint(const Sequence &seq1, const Sequence &seq2);

  static std::vector<int> slack_weights(const int capacity);
  GRBQuadExpr squared_penalty(const GRBLinExpr &expr) const;
  GRBQuadExpr build_capacity_penalty();
  void update_qubo_objective(const double penalty, const bool verbose);
  void print_ip_debug() const;
  void capture_qubo();

  BayState bayState;
  std::vector<std::vector<Sequence>> sequence;
  std::vector<std::vector<std::vector<std::vector<bool>>>> conflict;
  std::vector<int> lastNumberOfSequences;
  std::vector<GRBLinExpr> qObjective;
  std::vector<GRBConstr> assignmentConstraint;
  std::vector<GRBLinExpr> qAssignmentConstraint;
  std::vector<std::vector<GRBConstr *>> capacityConstraint;
  std::vector<std::vector<std::vector<GRBVar>>> capacitySlackVars;
  std::vector<std::vector<std::vector<GRBConstr *>>> conflictConstraint;
  std::vector<std::vector<std::vector<GRBQuadExpr *>>> qConflictConstraint;
  GRBModel *model;
  GRBModel *qmodel;
  GRBConstr lowerBoundConstraint;

  // snapshot of qmodel's final objective, captured by capture_qubo() just
  // before qmodel is torn down at the end of solve(); export_qubo() reads
  // only this, so it stays valid after solve() has returned.
  bool quboCaptured;
  std::vector<std::string> quboVariableNames;
  // per-variable blocking-block priority, cost (Sequence::length()) and
  // relocation path, indexed the same way as quboVariableNames; captured
  // alongside it so export_qubo() can also dump the relocations, letting an
  // external tool (e.g. a Python script checking a quantum-computer
  // bitstring) rebuild relocation diagrams without re-running the solver.
  std::vector<int> quboVariablePriority;
  std::vector<int> quboVariableCost;
  std::vector<std::vector<Relocation>> quboVariableRelocations;
  std::map<std::pair<int, int>, double> quboCoefficients;

  int initialNumberOfVariables, initialNumberOfConstraints;
  int finalNumberOfVariables, finalNumberOfConstraints;
  int lowerBound, upperBound;

  double totalTime, LBTime, UBTime;
  int iteration;
  std::vector<int> solution;
  std::vector<int> solutionUB;
  Solution *bestSolution;
};

#endif /* !_QUBOMODEL_HPP_ */
