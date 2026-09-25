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
#include <fstream>
#include <iostream>
#include <iomanip>
#include <map>
#include <set>
#include <utility>
#include <vector>
#include <list>
#include <chrono>
#include "gurobi_c++.h"

#include "greedy.hpp"
#include "qubomodel.hpp"

bool QUBOModel::solve(const Parameter &parameter)
{
  if (bayState.blockingBlock.size() == 0)
  {
    lowerBound = upperBound = 0;
    return true;
  }

  GRBEnv *env;
  GRBModel *m;
  GRBModel *qm;

  try
  {
    env = new GRBEnv();
    m = new GRBModel(*env);
    qm = new GRBModel(*env);
  }
  // catch (GRBException e)
  catch (const GRBException &e)
  {
    std::cerr << "Error code=" << e.getErrorCode() << std::endl;
    std::cerr << e.getMessage() << std::endl;
    return false;
  }

  model = m;
  qmodel = qm;
  if (parameter.verbose < 2)
  {
    model->set(GRB_IntParam_OutputFlag, 0);
    qmodel->set(GRB_IntParam_OutputFlag, 0);
  }
  model->set(GRB_IntParam_Threads, parameter.numberOfThreads);
  qmodel->set(GRB_IntParam_Threads, parameter.numberOfThreads);

  auto start = std::chrono::system_clock::now();

  if (parameter.greedyUpperBound)
  {
    bestSolution = greedy(bayState[0]);
    std::cerr << "greedy_upper_bound=" << bestSolution->number_of_relocations()
              << std::endl;
  }

  model->setObjective(static_cast<GRBLinExpr>(0.0), GRB_MINIMIZE);
  qmodel->setObjective(static_cast<GRBQuadExpr>(0.0), GRB_MINIMIZE);

  // assignment constraints
  for (const auto &bb : bayState.blockingBlock)
  {
    std::ostringstream ss;
    ss << "a(" << bb.priority << ")";
    assignmentConstraint[bb.priority] = model->addConstr(static_cast<GRBLinExpr>(0.0), GRB_EQUAL, 1.0,
                                                         ss.str());
  }
  // std::cout << "assignment constraints ok" << std::endl;

  // capacity constraints
  if (bayState.numberOfTiers < bayState.numberOfBlocks)
  {
    for (int p = 0; p < bayState.numberOfBlocks - bayState.numberOfTiers - 1;
         ++p)
    {
      capacityConstraint[p].resize(bayState.numberOfStacks, nullptr);
      capacitySlackVars[p].resize(bayState.numberOfStacks);
    }
  }
  // std::cout << "capacity constraints ok" << std::endl;

  // conflict constraints
  for (const auto &bb : bayState.blockingBlock)
  {
    sequence[bb.priority].reserve(100);
    conflict[bb.priority].resize(bb.priority);
    conflictConstraint[bb.priority].resize(bb.priority);
  }

  // lower bound constraint
  lowerBoundConstraint = model->addConstr(0.0, GRB_GREATER_EQUAL,
                                          lowerBound, "LB");

  // initial sequences
  for (const auto &bb : bayState.blockingBlock)
  {
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
  if (bayState.numberOfTiers < bayState.numberOfBlocks)
  {
    update_capacity_constraints();
  }
  update_lower_bound();
  update_last_numbers();
  model->update();

  qmodel->update();

  initialNumberOfVariables = model->get(GRB_IntAttr_NumVars);
  initialNumberOfConstraints = model->get(GRB_IntAttr_NumConstrs);

  bool solved = false;
  for (iteration = 1;; ++iteration)
  {
    auto current = std::chrono::system_clock::now();
    auto duration = current - start;
    auto msec = static_cast<double>(std::chrono::
                                        duration_cast<std::chrono::milliseconds>(duration)
                                            .count());

    std::cerr << "iteration=" << iteration << ", time=";
    std::cerr << std::fixed << std::setprecision(2) << std::showpoint;
    std::cerr << (0.001 * msec) << std::endl;

    if (parameter.timeLimit > 0.0)
    {
      if (parameter.timeLimit < 0.001 * msec)
      {
        break;
      }
      model->set(GRB_DoubleParam_TimeLimit, parameter.timeLimit - 0.001 * msec);
      qmodel->set(GRB_DoubleParam_TimeLimit, parameter.timeLimit - 0.001 * msec);
    }

    // --------- add QUBO begin ----------------------------

    if (parameter.verbose > 1)
    {
      print_ip_debug();
    }

    update_qubo_objective(parameter.verbose > 1);

    printf("\n\n----------------- IP optimization begin ---------------------------\n\n");
    model->optimize();
    printf("\n----------------- IP optimization end ---------------------------\n\n");
    if (parameter.verbose > 1)
    {
      std::cout << "IP solution: ";
      GRBVar *model_vars = model->getVars();
      for (int j = 0; j < model->get(GRB_IntAttr_NumVars); j++)
        if (model_vars[j].get(GRB_DoubleAttr_X) > 0)
        {
          std::cout << model_vars[j].get(GRB_StringAttr_VarName) << "   ";
        }
      std::cout << std::endl;
    }

    printf("\n\n----------------- QUBO optimization begin ---------------------------\n\n");
    qmodel->optimize();
    printf("\n----------------- QUBO optimization end ---------------------------\n\n");
    report_qubo("ub");
    report_qubo("lb");
    if (parameter.verbose > 1)
    {
      std::cout << "QUBO solution: ";
      GRBVar *qmodel_vars = qmodel->getVars();
      for (int j = 0; j < qmodel->get(GRB_IntAttr_NumVars); j++)
        if (qmodel_vars[j].get(GRB_DoubleAttr_X) > 0)
        {
          std::cout << qmodel_vars[j].get(GRB_StringAttr_VarName) << "   ";
        }
      std::cout << std::endl;
    }

    // --------- add QUBO end ----------------------------

    LBTime += model->get(GRB_DoubleAttr_Runtime);

    solution.assign(bayState.numberOfBlocks, -1);

    int optimstatus = model->get(GRB_IntAttr_Status);
    if (optimstatus != GRB_OPTIMAL)
    {
      break;
    }

    lowerBound = static_cast<int>(model->get(GRB_DoubleAttr_ObjVal) + 0.5);
    std::cerr << "lower_bound=" << lowerBound << std::endl;

    for (const auto &bb : bayState.blockingBlock)
    {
      for (auto &seq : sequence[bb.priority])
      {
        if (seq.type != Inactive && (seq.variable)->get(GRB_DoubleAttr_X) >= 0.5)
        {
          solution[bb.priority] = seq.no;
          if (parameter.verbose > 0)
          {
            std::cout << seq << std::endl;
          }
          break;
        }
      }
    }

    if (is_solution_optimal() || lowerBound == upperBound || (bestSolution != nullptr && lowerBound == bestSolution->number_of_relocations()))
    {
      if (bestSolution != nullptr)
      {
        if (upperBound < bestSolution->number_of_relocations() || lowerBound < bestSolution->number_of_relocations())
        {
          delete bestSolution;
          bestSolution = nullptr;
        }
        else
        {
          upperBound = bestSolution->number_of_relocations();
        }
      }

      if (lowerBound != upperBound)
      {
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
    if (bayState.numberOfTiers < bayState.numberOfBlocks)
    {
      update_capacity_constraints();
    }
    update_lower_bound();
    update_last_numbers();

    if (parameter.upperBounding == false)
    {
      continue;
    }

    fix_variables_ub();

#if 1
    // a trick to detect the infeasibility correctly
    if (upperBound == bayState.numberOfBlocks * bayState.numberOfTiers)
    {
      model->set(GRB_IntParam_Aggregate, 0);
    }
#endif

    current = std::chrono::system_clock::now();
    duration = current - start;
    msec = static_cast<double>(std::chrono::
                                   duration_cast<std::chrono::milliseconds>(duration)
                                       .count());

    if (parameter.timeLimit > 0.0)
    {
      if (parameter.timeLimit < 0.001 * msec)
      {
        break;
      }
      model->set(GRB_DoubleParam_TimeLimit, parameter.timeLimit - 0.001 * msec);
      qmodel->set(GRB_DoubleParam_TimeLimit, parameter.timeLimit - 0.001 * msec);
    }

    // --------- add QUBO begin ----------------------------

    model->update();
    qmodel->update();

    if (parameter.verbose > 1)
    {
      print_ip_debug();
    }

    update_qubo_objective(parameter.verbose > 1);

    printf("\n\n----------------- IP optimization begin ---------------------------\n\n");
    model->optimize();
    printf("\n----------------- IP optimization end ---------------------------\n\n");
    if (parameter.verbose > 1)
    {
      std::cout << "IP solution: ";
      GRBVar *model_vars = model->getVars();
      for (int j = 0; j < model->get(GRB_IntAttr_NumVars); j++)
        if (model_vars[j].get(GRB_DoubleAttr_X) > 0)
        {
          std::cout << model_vars[j].get(GRB_StringAttr_VarName) << "   ";
        }
      std::cout << std::endl;
    }

    printf("\n\n----------------- QUBO optimization begin ---------------------------\n\n");
    qmodel->optimize();
    printf("\n----------------- QUBO optimization end ---------------------------\n\n");
    if (parameter.verbose > 1)
    {
      std::cout << "QUBO solution: ";
      GRBVar *qmodel_vars = qmodel->getVars();
      for (int j = 0; j < qmodel->get(GRB_IntAttr_NumVars); j++)
        if (qmodel_vars[j].get(GRB_DoubleAttr_X) > 0)
        {
          std::cout << qmodel_vars[j].get(GRB_StringAttr_VarName) << "   ";
        }
      std::cout << std::endl;
    }

    // --------- add QUBO end ----------------------------

    optimstatus = model->get(GRB_IntAttr_Status);

#if 1
    // fail safe
    if (optimstatus == GRB_OPTIMAL && upperBound == bayState.numberOfBlocks * bayState.numberOfTiers && !is_solution_feasible())
    {
      UBTime += model->get(GRB_DoubleAttr_Runtime);
      model->set(GRB_IntParam_Presolve, 0);
      model->reset();
      model->optimize();
      optimstatus = model->get(GRB_IntAttr_Status);
      model->set(GRB_IntParam_Presolve, -1);

      qmodel->set(GRB_IntParam_Presolve, 0);
      qmodel->reset();
      qmodel->optimize();
      qmodel->set(GRB_IntParam_Presolve, -1);
    }
#endif

    UBTime += model->get(GRB_DoubleAttr_Runtime);

    if (optimstatus == GRB_OPTIMAL)
    {
      int ub = static_cast<int>(model->get(GRB_DoubleAttr_ObjVal) + 0.5);
      upperBound = std::min(upperBound, ub);
      std::cerr << "upper_bound=" << ub << std::endl;
      model->set(GRB_IntParam_Aggregate, 1);
      solutionUB.assign(bayState.numberOfBlocks, -1);
      for (const auto &bb : bayState.blockingBlock)
      {
        for (auto &seq : sequence[bb.priority])
        {
          if (seq.type != Inactive && (seq.variable)->get(GRB_DoubleAttr_X) >= 0.5)
          {
            if (parameter.verbose > 0)
            {
              std::cout << seq << std::endl;
            }
            solutionUB[bb.priority] = seq.no;
            break;
          }
        }
      }
      if (lowerBound == upperBound)
      {
        solved = true;
        if (bestSolution != nullptr)
        {
          delete bestSolution;
          bestSolution = nullptr;
        }
        break;
      }
    }
    else if (optimstatus == GRB_INFEASIBLE)
    {
      std::cerr << "upper_bound=infeasible" << std::endl;
    }
    else
    {
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
  auto msec = static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(duration).count());

  totalTime += msec * 0.001;

  // The loop can add sequences after its last objective rebuild, which would
  // leave those variables in qmodel but outside the objective -- an exported
  // model whose one-hot groups are incomplete. Rebuild once more so the export
  // always matches the final variable set (and gets the tightest pruning, the
  // upper bound now being final).
  update_qubo_objective(false);
  capture_qubo();

  delete m;
  delete qm;
  delete env;

  if (!solved && bestSolution != nullptr)
  {
    if (upperBound < bestSolution->number_of_relocations())
    {
      delete bestSolution;
      bestSolution = nullptr;
    }
    else
    {
      upperBound = bestSolution->number_of_relocations();
    }
  }

  return solved;
}

Solution *QUBOModel::best_solution()
{
  if (bestSolution != nullptr)
  {
    return bestSolution;
  }

  if (upperBound < bayState.numberOfBlocks * bayState.numberOfTiers)
  {
    std::vector<std::list<Relocation>> s;
    s.resize(bayState.numberOfBlocks);
    for (auto &bb : bayState.blockingBlock)
    {
      s[bb.priority] = sequence[bb.priority][solutionUB[bb.priority]].relocations;
    }
    bestSolution = new Solution(bayState[0], s);
    if (bestSolution->number_of_relocations() != upperBound)
    {
      delete bestSolution;
      bestSolution = nullptr;
      throw "Error: infeasible solution";
    }
  }

  return bestSolution;
}

#define Tolerance (1.0e-4)

bool QUBOModel::is_solution_feasible() const
{
  GRBConstr *c = model->getConstrs();

  for (int n = 0; n < model->get(GRB_IntAttr_NumConstrs); ++n)
  {
    char sense = c[n].get(GRB_CharAttr_Sense);

    if (sense == '>' && c[n].get(GRB_DoubleAttr_Slack) >= Tolerance)
    {
      return false;
    }
    else if (sense == '<' && c[n].get(GRB_DoubleAttr_Slack) <= -Tolerance)
    {
      return false;
    }
    else if (sense == '=' && (c[n].get(GRB_DoubleAttr_Slack) <= -Tolerance || c[n].get(GRB_DoubleAttr_Slack) >= Tolerance))
    {
      return false;
    }
  }
  return true;
}

bool QUBOModel::is_solution_optimal() const
{
  for (const auto &bb : bayState.blockingBlock)
  {
    if (sequence[bb.priority][solution[bb.priority]].type != NonBlocking)
    {
      return false;
    }
  }
  return true;
}

void QUBOModel::fix_variables_ub()
{
  for (const auto &bb : bayState.blockingBlock)
  {
    for (const auto &seq : sequence[bb.priority])
    {
      if (seq.type == Inactive)
      {
        continue;
      }
      if (seq.type == Blocking)
      {
        (seq.variable)->set(GRB_DoubleAttr_UB, 0.0);
      }
      else if (seq.no == solutionUB[bb.priority])
      {
        (seq.variable)->set(GRB_DoubleAttr_Start, 1.0);
      }
      else if (solutionUB[bb.priority] != -1)
      {
        (seq.variable)->set(GRB_DoubleAttr_Start, 0.0);
      }
    }
  }
}

void QUBOModel::unfix_variables_ub()
{
  for (const auto &bb : bayState.blockingBlock)
  {
    for (const auto &seq : sequence[bb.priority])
    {
      if (seq.type == Blocking)
      {
        (seq.variable)->set(GRB_DoubleAttr_UB, 1.0);
      }
    }
  }
}

void QUBOModel::expand_solution(const int threshold, const bool verbose)
{
  if (verbose)
  {
    std::cout << "Sequences to be expanded:" << std::endl;
  }

  for (const auto &bb1 : bayState.blockingBlock)
  {
    const int b1 = bb1.priority;
    if (sequence[b1][solution[b1]].type != NonBlocking && sequence[b1][solution[b1]].type != Inactive)
    {
      Sequence seq(sequence[b1][solution[b1]]);
      if (verbose)
      {
        std::cout << seq << std::endl;
      }
      expand_sequence(seq, threshold);
      Sequence &origseq = sequence[b1][solution[b1]];
      origseq.type = Inactive;
      std::cout << "Removing variable " << origseq.qvariable->get(GRB_StringAttr_VarName) << std::endl;
      model->remove(*(origseq.variable));

      qmodel->remove(*(origseq.qvariable));
      /*GRBQuadExpr new_objective = qmodel->getObjective();
      GRBLinExpr lin_objective = qmodel->getObjective().getLinExpr();
      new_objective -= lin_objective;
      lin_objective.remove(*(origseq.qvariable));
      new_objective += lin_objective;
      new_objective.remove(*(origseq.qvariable));
      qmodel->setObjective(new_objective);
      qmodel->update();*/

      for (const auto &bb2 : bayState.blockingBlock)
      {
        const int b2 = bb2.priority;
        if (b2 < b1 && conflictConstraint[b1][b2][origseq.no] != nullptr)
        {
          model->remove(*(conflictConstraint[b1][b2][origseq.no]));
          conflictConstraint[b1][b2][origseq.no] = nullptr;
        }
      }
    }
  }
  if (verbose)
  {
    std::cout << std::endl;
  }
}

void QUBOModel::expand_sequence(Sequence &srcSequence, const int threshold,
                                const int depth)
{
  const BlockingBlock &bb = srcSequence.block;
  const int period = srcSequence.last_relocation().period;
  const int src = srcSequence.last_relocation().src;
  const int remainingRelocations = srcSequence.remainingRelocations;

  if (remainingRelocations == 1)
  {
    int nextRelocationBlockingStacks = 0;
    int nextRelocationNonBlockingSlackStacks = 0;

    for (int s = 0; s < bayState.numberOfStacks; ++s)
    {
      if (s == src || bayState[period].stack[s].height == bayState.numberOfTiers)
      {
        // source stack or stack is full
        continue;
      }

      srcSequence.change_last_destination(s);

      if (bb.priority < bayState[period].stack[s].minimumPriority)
      {
        add_new_sequence(NonBlocking, 0, srcSequence);
        Sequence &seq = sequence[bb.priority].back();
        seq.add_relocation(bb.priority, s, -1);
        if (bayState[period].stack[s].height < bayState.numberOfTiers - 1)
        {
          ++nextRelocationNonBlockingSlackStacks;
        }
      }
      else
      {
        ++nextRelocationBlockingStacks;
      }
    }

    srcSequence.change_last_destination(-2);

    if (nextRelocationBlockingStacks > 0 || (nextRelocationNonBlockingSlackStacks > 0 && bayState.precedingBlocks[bb.priority][period].size() > 0))
    {
      // at least one blocking sequence remains
      if (depth == 1)
      {
        add_new_sequence(Blocking, 2, srcSequence);
      }
      else
      {
        srcSequence.remainingRelocations = 2;
        expand_sequence(srcSequence, threshold, depth - 1);
        srcSequence.remainingRelocations = 1;
      }
    }

    return;
  }

  for (int s = 0; s < bayState.numberOfStacks; ++s)
  {
    if (s == src || bayState[period].stack[s].height == bayState.numberOfTiers)
    {
      // source stack or stack is full
      continue;
    }

    std::vector<int> nextPossiblePeriods;

    if (bayState[period].stack[s].height < bayState.numberOfTiers - 1)
    {
      // previously relocated block may be below this block
      for (auto pr = bayState.precedingBlocks[bb.priority][period].begin();
           pr != bayState.precedingBlocks[bb.priority][period].end() && *pr < bayState[period].stack[s].minimumPriority; ++pr)
      {
        nextPossiblePeriods.push_back(*pr);
      }
    }

    if (bayState[period].stack[s].minimumPriority < bb.priority)
    {
      // always becomes blocking
      nextPossiblePeriods.push_back(bayState[period].stack[s].minimumPriority);
    }

    srcSequence.change_last_destination(s);

    for (auto p : nextPossiblePeriods)
    {
      int nextRelocationBlockingStacks = 0;
      int nextRelocationNonBlockingSlackStacks = 0;
      std::vector<int> nextRelocationNonBlockingStacks;

      for (int s2 = 0; s2 < bayState.numberOfStacks; ++s2)
      {
        if (s2 != s && bayState[p].stack[s2].height < bayState.numberOfTiers)
        {
          if (bayState[p].stack[s2].minimumPriority < bb.priority)
          {
            ++nextRelocationBlockingStacks;
          }
          else
          {
            nextRelocationNonBlockingStacks.push_back(s2);
            if (bayState[p].stack[s2].height < bayState.numberOfTiers - 1)
            {
              ++nextRelocationNonBlockingSlackStacks;
            }
          }
        }
      }

      srcSequence.add_relocation(p, s, -2);

      if (nextRelocationBlockingStacks > 0 || (nextRelocationNonBlockingSlackStacks > 0 && bayState.precedingBlocks[bb.priority][p].size() > 0))
      {
        if (static_cast<int>(nextRelocationNonBlockingStacks.size()) <= threshold)
        {
          for (const auto &s2 : nextRelocationNonBlockingStacks)
          {
            add_new_sequence(NonBlocking, 0, srcSequence);
            Sequence &nseq = sequence[bb.priority].back();
            nseq.change_last_destination(s2);
            nseq.add_relocation(bb.priority, s2, -1);
          }

          if (depth == 1)
          {
            if (nextRelocationNonBlockingStacks.empty())
            {
              add_new_sequence(Blocking,
                               1 + remaining_relocations(srcSequence),
                               srcSequence);
            }
            else
            {
              add_new_sequence(Blocking, 2, srcSequence);
            }
          }
          else
          {
            srcSequence.remainingRelocations = 2;
            expand_sequence(srcSequence, threshold, depth - 1);
          }
        }
        else if (depth == 1)
        {
          add_new_sequence(Blocking, 1, srcSequence);
        }
        else
        {
          srcSequence.remainingRelocations = 1;
          expand_sequence(srcSequence, threshold, depth - 1);
        }
      }
      else
      {
        // all sequences are nonblocking
        for (const auto &s2 : nextRelocationNonBlockingStacks)
        {
          add_new_sequence(NonBlocking, 0, srcSequence);
          Sequence &nseq = sequence[bb.priority].back();
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

int QUBOModel::remaining_relocations(Sequence &srcSequence)
{
  const BlockingBlock &bb = srcSequence.block;
  int period = srcSequence.last_relocation().period, p;
  int src = srcSequence.last_relocation().src, dst;
  int remaining;

  for (remaining = 0;; ++remaining, src = dst, period = p)
  {
    p = dst = -1;
    for (int s = 0; s < bayState.numberOfStacks; ++s)
    {
      if (s != src && bayState[period].stack[s].height < bayState.numberOfTiers && bayState[period].stack[s].minimumPriority > p)
      {
        dst = s;
        p = bayState[period].stack[s].minimumPriority;
      }
    }
    if (p > bb.priority)
    {
      break;
    }
  }

  return remaining;
}

int QUBOModel::earliest_period(const Sequence srcSequence,
                               const int remainingRelocations)
{
  if (srcSequence.type == NonBlocking)
  {
    return 0;
  }

  int t = srcSequence.relocations.back().period;
  int src = srcSequence.relocations.back().src;

  for (int n = remainingRelocations - 1; n > 0; --n)
  {
    std::vector<int> tmp = bayState.precedingBlocks[srcSequence.block.priority][t];
    int minimumPreceding;
    int dst = -1, minimumPriority = srcSequence.block.priority;

    if (tmp.empty())
    {
      minimumPreceding = bayState.numberOfBlocks;
    }
    else
    {
      minimumPreceding = *std::min_element(tmp.begin(), tmp.end());
    }
    for (int s = 0; s < bayState.numberOfStacks; ++s)
    {
      if (s != src && bayState[t].stack[s].height < bayState.numberOfTiers && bayState[t].stack[s].minimumPriority < minimumPriority)
      {
        dst = src;
        minimumPriority = bayState[t].stack[s].minimumPriority;
      }
    }

    if (minimumPreceding < minimumPriority)
    {
      t = minimumPreceding;
      src = -1;
    }
    else
    {
      t = minimumPriority;
      src = dst;
    }
  }

  return t;
}

void QUBOModel::add_variables()
{
  for (const auto &bb : bayState.blockingBlock)
  {
    for (int sq = lastNumberOfSequences[bb.priority];
         sq < static_cast<int>(sequence[bb.priority].size()); ++sq)
    {
      std::ostringstream ss;
      ss << "x(" << bb.priority << "," << sq << ")";
      sequence[bb.priority][sq]
          .set_variable(model->addVar(0.0, 1.0,
                                      static_cast<double>(sequence[bb.priority]
                                                                  [sq]
                                                                      .length()),
                                      GRB_BINARY, ss.str()));
      model->chgCoeff(assignmentConstraint[bb.priority],
                      *(sequence[bb.priority][sq].variable), 1.0);
      model->chgCoeff(lowerBoundConstraint,
                      *(sequence[bb.priority][sq].variable),
                      static_cast<double>(sequence[bb.priority][sq].length()));

      sequence[bb.priority][sq]
          .set_qvariable(qmodel->addVar(0.0, 1.0,
                                        static_cast<double>(sequence[bb.priority]
                                                                    [sq]
                                                                        .length()),
                                        GRB_BINARY, ss.str()));
    }
  }
}

bool QUBOModel::check_conflict(const Sequence *sq1, const Sequence *sq2)
{
  std::list<Relocation>::const_iterator a_itr, b_itr, a_end, b_end;
  const Sequence *a_seq;
  const Sequence *b_seq;

  if (sq1->block.priority < sq2->block.priority)
  {
    a_seq = sq1;
    b_seq = sq2;
    a_itr = sq1->relocations.begin();
    b_itr = sq2->relocations.begin();
    a_end = sq1->relocations.end();
    b_end = sq2->relocations.end();
  }
  else
  {
    a_seq = sq2;
    b_seq = sq1;
    a_itr = sq2->relocations.begin();
    b_itr = sq1->relocations.begin();
    a_end = sq2->relocations.end();
    b_end = sq1->relocations.end();
  }

  if (a_seq->type == NonBlocking && a_seq->block.priority <= b_itr->period)
  {
    return false;
  }

  int above = 0; // 1: a is above b, -1: b is above a, 0: unrelated
  if (a_itr->src == b_itr->src)
  {
    if (a_itr->period < b_itr->period)
    {
      above = 1;
    }
    else if (a_itr->period > b_itr->period)
    {
      above = -1;
    }
    else if (a_seq->block.no < b_seq->block.no)
    {
      above = 1;
    }
    else
    {
      above = -1;
    }
  }
  else
  {
    above = 0;
  }

  while (a_itr != a_end && b_itr != b_end)
  {
    if (a_itr->period < b_itr->period)
    {
      if (above == -1)
      {
        // b is above a
        // b should be relocated in the same period
        return true;
      }
      above = (a_itr->dst == b_itr->src) ? 1 : 0;
      ++a_itr;
    }
    else if (b_itr->period < a_itr->period)
    {
      if (above == 1)
      {
        // a is above b
        // a should be relocated in the same period
        return true;
      }
      above = (b_itr->dst == a_itr->src) ? -1 : 0;
      ++b_itr;
    }
    else
    {
      // a and b are relocated in the same period
      if (above == 0)
      {
        // above = 0 implies a and b are in different stacks
        return true;
      }
      else if (a_itr->period == a_seq->block.priority && above != -1)
      {
        // a is retrieved
        // b is relocated in the same period, implying b is above a
        return true;
      }

      if (a_itr->dst == b_itr->dst)
      {
        above = -above;
      }
      else if (a_itr->dst == -2)
      {
        if (above == -1)
        {
          // a is never below b in period t
          above = 2;
        }
        else
        {
          // arbitrary
          above = -2;
        }
      }
      else
      {
        above = 0;
      }
      ++a_itr;
      ++b_itr;
    }
  }

  if (a_itr == a_end)
  {
    for (; b_itr != b_end && b_itr->period < a_seq->block.priority; ++b_itr)
      ;
    if (b_itr == b_end || b_itr->period != a_seq->block.priority)
    {
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

    if (above != -2 && b_itr->period <= a_itr->period)
    {
      return true;
    }

    int n = a_seq->remainingRelocations;
    if (a_itr->src == b_itr->dst)
    {
      n = std::max(2, n);
    }

    if (b_itr->period < earliest_period(*a_seq, n))
    {
      return true;
    }
  }

  return false;
}

void QUBOModel::update_conflict_constraints()
{
  for (const auto &bb1 : bayState.blockingBlock)
  {
    const int b1 = bb1.priority;
    for (const auto &bb2 : bayState.blockingBlock)
    {
      const int b2 = bb2.priority;
      if (b2 < b1)
      {
        std::vector<std::vector<bool>> &conflictp = conflict[b1][b2];
        std::vector<GRBConstr *> &conflictConstraintp = conflictConstraint[b1][b2];
        conflictp.resize(sequence[b1].size());
        conflictConstraintp.resize(sequence[b1].size(), nullptr);

#if 1
        if (solution[b1] >= 0)
        {
          for (int sq = 0; sq < lastNumberOfSequences[b1]; ++sq)
          {
            conflictp[sq].resize(sequence[b2].size(), false);
          }
          for (int sq = lastNumberOfSequences[b1];
               sq < static_cast<int>(sequence[b1].size()); ++sq)
          {
            conflictp[sq] = conflictp[solution[b1]];
            conflictp[sq].resize(sequence[b2].size(), false);
          }
        }
        else
        {
          for (int sq = 0; sq < static_cast<int>(sequence[b1].size()); ++sq)
          {
            conflictp[sq].resize(sequence[b2].size(), false);
          }
        }
#else
        for (int sq = 0; sq < static_cast<int>(sequence[b1].size()); ++sq)
        {
          conflictp[sq].resize(sequence[b2].size(), false);
        }
#endif
      }
    }
  }

  for (const auto &bb1 : bayState.blockingBlock)
  {
    const int b1 = bb1.priority;
    for (const auto &bb2 : bayState.blockingBlock)
    {
      const int b2 = bb2.priority;
      if (b1 == b2)
      {
        continue;
      }
      for (int sq1 = lastNumberOfSequences[b1];
           sq1 < static_cast<int>(sequence[b1].size()); ++sq1)
      {
        for (int sq2 = 0; sq2 < static_cast<int>(sequence[b2].size()); ++sq2)
        {
          if (sequence[b2][sq2].type == Inactive)
          {
            continue;
          }
          if (b2 < b1)
          {
            if (!conflict[b1][b2][sq1][sq2])
            {
              conflict[b1][b2][sq1][sq2] = check_conflict(&(sequence[b2][sq2]), &(sequence[b1][sq1]));
            }
            if (conflict[b1][b2][sq1][sq2])
            {
              add_conflict_constraint(sequence[b1][sq1], sequence[b2][sq2]);
            }
          }
          else if (!conflict[b2][b1][sq2][sq1])
          {
            if ((conflict[b2][b1][sq2][sq1] = check_conflict(&(sequence[b1][sq1]), &(sequence[b2][sq2]))))
            {
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

void QUBOModel::update_capacity_constraints()
{
  for (const auto &bb : bayState.blockingBlock)
  {
    for (int sq = lastNumberOfSequences[bb.priority];
         sq < static_cast<int>(sequence[bb.priority].size()); ++sq)
    {
      const Sequence &seq = sequence[bb.priority][sq];
      auto itr = seq.relocations.begin();
      int t = itr->period;
      for (++itr; itr != seq.relocations.end(); ++itr)
      {
        for (; t < itr->period && t < bayState.numberOfBlocks - bayState.numberOfTiers - 1;
             ++t)
        {
          add_capacity_constraint(t, itr->src, sequence[bb.priority][sq]);
        }
      }
    }
  }
}

void QUBOModel::update_lower_bound()
{
  lowerBoundConstraint.set(GRB_DoubleAttr_RHS, static_cast<double>(lowerBound));
}

void QUBOModel::add_capacity_constraint(const int period, const int stack,
                                        const Sequence &seq)
{
  if (capacityConstraint[period][stack] == nullptr)
  {
    int slack = bayState.numberOfTiers - bayState[period].stack[stack].height;
    std::ostringstream ss;
    ss << "c(" << period << "," << stack << ")";
    capacityConstraint[period][stack] = new GRBConstr;
    *(capacityConstraint[period][stack]) = model->addConstr(*(seq.variable), GRB_LESS_EQUAL,
                                                            static_cast<double>(slack),
                                                            ss.str());
  }
  else
  {
    model->chgCoeff(*(capacityConstraint[period][stack]),
                    *(seq.variable), 1.0);
  }
}

void QUBOModel::add_conflict_constraint(const Sequence &seq1,
                                        const Sequence &seq2)
{
  int b1 = seq1.block.priority, b2 = seq2.block.priority;

  if (conflictConstraint[b1][b2][seq1.no] == nullptr)
  {
    std::ostringstream ss;
    ss << "f({" << b1 << "," << seq1.no << "}," << b2 << ")";
    conflictConstraint[b1][b2][seq1.no] = new GRBConstr;
    *(conflictConstraint[b1][b2][seq1.no]) = model->addConstr(*(seq1.variable) + *(seq2.variable),
                                                              GRB_LESS_EQUAL, 1.0, ss.str());
  }
  else
  {
    model->chgCoeff(*(conflictConstraint[b1][b2][seq1.no]),
                    *(seq2.variable), 1.0);
  }
}

// Minimal binary weight decomposition of an integer capacity C into
// {1, 2, 4, ..., 2^(k-1), C-(2^(k-1)-1)}, so that every integer in [0, C]
// is reachable by some 0/1 combination of the weights. Used to encode the
// slack of a "sum x_i <= C" capacity constraint as binary QUBO variables.
std::vector<int> QUBOModel::slack_weights(const int capacity)
{
  std::vector<int> weights;
  int remaining = capacity;
  int w = 1;

  while (remaining > 0)
  {
    const int use = std::min(w, remaining);
    weights.push_back(use);
    remaining -= use;
    w *= 2;
  }

  return weights;
}

// Expands expr*expr into a GRBQuadExpr, folding x*x -> x for every binary
// variable x (i.e., every diagonal term), so the result is a valid QUBO
// penalty for "expr == 0".
GRBQuadExpr QUBOModel::squared_penalty(const GRBLinExpr &expr) const
{
  GRBQuadExpr squared = expr * expr;
  GRBQuadExpr result = squared;

  for (unsigned int i = 0; i < squared.size(); ++i)
  {
    if (squared.getVar1(i).sameAs(squared.getVar2(i)))
    {
      const double coefficient = squared.getCoeff(i);
      result.addTerm(-coefficient, squared.getVar1(i), squared.getVar2(i));
      result.addTerm(coefficient, squared.getVar1(i));
    }
  }

  return result;
}

// An upper bound on the optimum of the *current* sequence model: the incumbent
// if there is one, otherwise the cost of the most expensive selection. Any
// valid upper bound works; a tighter one buys smaller penalty weights.
int QUBOModel::qubo_upper_bound() const
{
  int bound = 0;
  for (const auto &bb : bayState.blockingBlock)
  {
    int worst = 0;
    for (const auto &seq : sequence[bb.priority])
    {
      if (seq.type != Inactive)
      {
        worst = std::max(worst, seq.length());
      }
    }
    bound += worst;
  }
  if (bestSolution != nullptr)
  {
    bound = std::min(bound, bestSolution->number_of_relocations());
  }
  if (upperBound < bayState.numberOfBlocks * bayState.numberOfTiers)
  {
    bound = std::min(bound, upperBound);
  }
  return bound;
}

// Drops sequence variables that cannot appear in any selection at least as good
// as the incumbent: a selection containing sq costs at least length(sq) plus the
// cheapest sequence of every other blocking block. The cheapest sequence of each
// block always survives (their sum is a lower bound on the optimum, hence on the
// bound used here), so no group is ever emptied and the model's optimum is kept.
void QUBOModel::prune_qubo_variables(const bool verbose)
{
  const int bound = qubo_upper_bound();
  int minimumTotal = 0;
  std::vector<int> cheapest(bayState.numberOfBlocks, 0);

  for (const auto &bb : bayState.blockingBlock)
  {
    int best = -1;
    quboPruned[bb.priority].assign(sequence[bb.priority].size(), 0);
    for (const auto &seq : sequence[bb.priority])
    {
      if (seq.type != Inactive && (best < 0 || seq.length() < best))
      {
        best = seq.length();
      }
    }
    cheapest[bb.priority] = std::max(best, 0);
    minimumTotal += cheapest[bb.priority];
  }

  int pruned = 0;
  for (const auto &bb : bayState.blockingBlock)
  {
    const int others = minimumTotal - cheapest[bb.priority];
    for (auto &seq : sequence[bb.priority])
    {
      if (seq.type == Inactive)
      {
        continue;
      }
      if (seq.length() + others > bound)
      {
        quboPruned[bb.priority][seq.no] = 1;
        ++pruned;
      }
    }
  }

  // Second pass: a sequence dominated by another of the same block cannot be
  // needed either. If sq1 costs no more than sq2, conflicts with nothing sq2
  // does not conflict with, and passes through no stack-period sq2 does not,
  // then swapping sq2 for sq1 keeps every constraint satisfied at no extra
  // cost -- so an optimal selection using sq2 has an equally good twin using
  // sq1, and sq2 can go.
  int dominated = 0;
  for (const auto &bb : bayState.blockingBlock)
  {
    const int b = bb.priority;
    std::vector<std::vector<std::pair<int, int>>> buckets(sequence[b].size());
    for (const auto &seq : sequence[b])
    {
      if (qubo_active(seq))
      {
        buckets[static_cast<std::size_t>(seq.no)] = occupied_buckets(seq);
      }
    }

    for (std::size_t i = 0; i < sequence[b].size(); ++i)
    {
      for (std::size_t j = i + 1; j < sequence[b].size(); ++j)
      {
        if (!qubo_active(sequence[b][i]) || !qubo_active(sequence[b][j]))
        {
          continue;
        }
        const Sequence &a = sequence[b][i];
        const Sequence &c = sequence[b][j];
        // the cheaper one is the candidate dominator; equal costs keep the first
        const bool aFirst = (a.length() <= c.length());
        const Sequence &keeper = aFirst ? a : c;
        const Sequence &victim = aFirst ? c : a;
        const std::vector<std::pair<int, int>> &keeperBuckets =
            buckets[static_cast<std::size_t>(keeper.no)];
        const std::vector<std::pair<int, int>> &victimBuckets =
            buckets[static_cast<std::size_t>(victim.no)];

        if (!std::includes(victimBuckets.begin(), victimBuckets.end(),
                           keeperBuckets.begin(), keeperBuckets.end()))
        {
          continue;
        }

        bool dominates = true;
        for (const auto &bb2 : bayState.blockingBlock)
        {
          const int b2 = bb2.priority;
          if (b2 == b)
          {
            continue;
          }
          for (std::size_t sq2 = 0; dominates && sq2 < sequence[b2].size(); ++sq2)
          {
            if (!qubo_active(sequence[b2][sq2]))
            {
              continue;
            }
            if (conflicts_with(keeper, b2, static_cast<int>(sq2))
                && !conflicts_with(victim, b2, static_cast<int>(sq2)))
            {
              dominates = false;
            }
          }
          if (!dominates)
          {
            break;
          }
        }

        if (dominates)
        {
          quboPruned[b][victim.no] = 1;
          ++dominated;
        }
      }
    }
  }

  if (verbose && (pruned > 0 || dominated > 0))
  {
    std::cout << "QUBO dropped " << pruned << " sequence variable(s) at cost bound "
              << bound << " and " << dominated << " dominated one(s)" << std::endl;
  }
}

// Is this sequence in conflict with sequence `otherSequence` of `otherBlock`?
// The table is stored once per unordered block pair, under the higher priority.
bool QUBOModel::conflicts_with(const Sequence &seq, const int otherBlock,
                               const int otherSequence) const
{
  const int b = seq.block.priority;
  const int hi = std::max(b, otherBlock);
  const int lo = std::min(b, otherBlock);
  const std::size_t row = static_cast<std::size_t>(b == hi ? seq.no : otherSequence);
  const std::size_t col = static_cast<std::size_t>(b == hi ? otherSequence : seq.no);

  if (conflict[hi][lo].size() <= row || conflict[hi][lo][row].size() <= col)
  {
    return false;
  }
  return conflict[hi][lo][row][col];
}

// The (period, stack) buckets a sequence occupies: the capacity constraints it
// takes part in. Sorted, so set inclusion can be tested directly.
std::vector<std::pair<int, int>> QUBOModel::occupied_buckets(const Sequence &seq) const
{
  std::vector<std::pair<int, int>> buckets;
  auto itr = seq.relocations.begin();
  int t = itr->period;
  for (++itr; itr != seq.relocations.end(); ++itr)
  {
    for (; t < itr->period && t < bayState.numberOfBlocks - bayState.numberOfTiers - 1;
         ++t)
    {
      buckets.push_back(std::make_pair(t, itr->src));
    }
  }
  std::sort(buckets.begin(), buckets.end());
  buckets.erase(std::unique(buckets.begin(), buckets.end()), buckets.end());
  return buckets;
}

// One product term per conflicting pair of selected sequences: the pair
// constraint x1 + x2 <= 1 needs no slack, x1*x2 is already its exact penalty.
// Collected from the conflict table every iteration so that inactive and pruned
// sequences contribute nothing, and merged with the capacity pairs so a pair
// both families forbid carries one term rather than two.
void QUBOModel::collect_conflict_pairs(ForbiddenPairs &pairs) const
{
  for (const auto &bb1 : bayState.blockingBlock)
  {
    const int b1 = bb1.priority;
    for (const auto &bb2 : bayState.blockingBlock)
    {
      const int b2 = bb2.priority;
      if (b2 >= b1 || conflict[b1][b2].empty())
      {
        continue;
      }
      for (std::size_t sq1 = 0;
           sq1 < conflict[b1][b2].size() && sq1 < sequence[b1].size(); ++sq1)
      {
        if (!qubo_active(sequence[b1][sq1]))
        {
          continue;
        }
        for (std::size_t sq2 = 0;
             sq2 < conflict[b1][b2][sq1].size() && sq2 < sequence[b2].size(); ++sq2)
        {
          if (!conflict[b1][b2][sq1][sq2] || !qubo_active(sequence[b2][sq2]))
          {
            continue;
          }
          const GRBVar &v1 = *(sequence[b1][sq1].qvariable);
          const GRBVar &v2 = *(sequence[b2][sq2].qvariable);
          const int i = std::min(v1.index(), v2.index());
          const int j = std::max(v1.index(), v2.index());
          pairs[std::make_pair(i, j)] = std::make_pair(v1, v2);
        }
      }
    }
  }
}

// Builds the QUBO penalty for the capacity constraints (the relocations a stack
// receives during a period must fit in its free tiers). Every bucket is checked
// against the cheapest encoding that still forbids exactly the infeasible
// assignments:
//
//   * a bucket whose sequences come from at most `capacity` distinct blocking
//     blocks cannot be violated at all -- the one-hot constraints already cap
//     the sum -- so it is dropped, slack variables and all;
//   * a bucket implied by another one (its variables are a subset of the other's
//     and its capacity is no smaller) is dropped for the same reason;
//   * capacity 0 means every variable in the bucket must be 0, which is a linear
//     penalty and needs neither slack variables nor couplings;
//   * capacity 1 is "at most one of them", whose exact penalty is a product per
//     pair -- again no slack variables. Those pairs join the conflict pairs, so
//     a pair both families forbid is paid for once, and pairs from one blocking
//     block are skipped because the one-hot penalty already covers them;
//   * only capacity >= 2 falls back to slack variables and a squared penalty,
//     and identical buckets are encoded once.
//
// The slack variables are created per (period, stack) bucket and reused across
// iterations so repeated rebuilds do not leak variables into qmodel.
GRBQuadExpr QUBOModel::build_capacity_penalty(const double penalty,
                                              ForbiddenPairs &pairs)
{
  struct Entry
  {
    GRBVar var;
    int block;
  };
  struct Bucket
  {
    int period;
    int stack;
    int capacity;
    std::vector<Entry> members;
    std::vector<int> signature;      // sorted, unique variable indices
  };

  std::map<std::pair<int, int>, std::vector<Entry>> collected;

  for (const auto &bb : bayState.blockingBlock)
  {
    for (const auto &seq : sequence[bb.priority])
    {
      if (!qubo_active(seq))
      {
        continue;
      }

      auto itr = seq.relocations.begin();
      int t = itr->period;
      for (++itr; itr != seq.relocations.end(); ++itr)
      {
        for (; t < itr->period && t < bayState.numberOfBlocks - bayState.numberOfTiers - 1;
             ++t)
        {
          collected[std::make_pair(t, itr->src)]
              .push_back(Entry{*(seq.qvariable), bb.priority});
        }
      }
    }
  }

  std::vector<Bucket> buckets;
  for (auto &entry : collected)
  {
    Bucket bucket;
    bucket.period = entry.first.first;
    bucket.stack = entry.first.second;
    bucket.capacity = bayState.numberOfTiers
                      - bayState[bucket.period].stack[bucket.stack].height;
    bucket.members = entry.second;

    std::set<int> blocks;
    for (const auto &member : bucket.members)
    {
      blocks.insert(member.block);
      bucket.signature.push_back(member.var.index());
    }
    // one selection per blocking block: a bucket that cannot reach its capacity
    // is already enforced by the one-hot penalties
    if (static_cast<int>(blocks.size()) <= bucket.capacity)
    {
      continue;
    }
    std::sort(bucket.signature.begin(), bucket.signature.end());
    bucket.signature.erase(std::unique(bucket.signature.begin(), bucket.signature.end()),
                           bucket.signature.end());
    buckets.push_back(bucket);
  }

  // A bucket is redundant when another kept bucket covers its variables with no
  // more room: sum over the subset <= sum over the superset <= that capacity.
  // Identical buckets would otherwise drop each other, so among equals only the
  // first survives.
  std::vector<bool> keep(buckets.size(), true);
  for (std::size_t a = 0; a < buckets.size(); ++a)
  {
    for (std::size_t b = 0; keep[a] && b < buckets.size(); ++b)
    {
      if (a == b || !keep[b] || buckets[b].capacity > buckets[a].capacity)
      {
        continue;
      }
      const bool identical = (buckets[a].signature == buckets[b].signature
                              && buckets[a].capacity == buckets[b].capacity);
      if (identical && b > a)
      {
        continue;                    // keep the first of a set of equals
      }
      if (std::includes(buckets[b].signature.begin(), buckets[b].signature.end(),
                        buckets[a].signature.begin(), buckets[a].signature.end()))
      {
        keep[a] = false;
      }
    }
  }

  GRBQuadExpr result(0.0);

  for (std::size_t index = 0; index < buckets.size(); ++index)
  {
    if (!keep[index])
    {
      continue;
    }
    const Bucket &bucket = buckets[index];
    const std::vector<Entry> &members = bucket.members;

    if (bucket.capacity <= 0)
    {
      for (const auto &member : members)
      {
        result.addTerm(penalty, member.var);
      }
      continue;
    }

    if (bucket.capacity == 1)
    {
      for (std::size_t i = 0; i < members.size(); ++i)
      {
        for (std::size_t j = i + 1; j < members.size(); ++j)
        {
          if (members[i].block == members[j].block)
          {
            continue;                // the one-hot penalty already forbids this
          }
          const int a = std::min(members[i].var.index(), members[j].var.index());
          const int b = std::max(members[i].var.index(), members[j].var.index());
          pairs[std::make_pair(a, b)] = std::make_pair(members[i].var, members[j].var);
        }
      }
      continue;
    }

    const std::vector<int> weights = slack_weights(bucket.capacity);

    if (capacitySlackVars[bucket.period][bucket.stack].empty() && !weights.empty())
    {
      for (std::size_t k = 0; k < weights.size(); ++k)
      {
        std::ostringstream ss;
        ss << "s(" << bucket.period << "," << bucket.stack << "," << k << ")";
        capacitySlackVars[bucket.period][bucket.stack]
            .push_back(qmodel->addVar(0.0, 1.0, 0.0, GRB_BINARY, ss.str()));
      }
      qmodel->update();
    }

    GRBLinExpr expr(0.0);
    for (const auto &member : members)
    {
      expr += member.var;
    }
    for (std::size_t k = 0;
         k < capacitySlackVars[bucket.period][bucket.stack].size() && k < weights.size(); ++k)
    {
      expr += static_cast<double>(weights[k])
              * capacitySlackVars[bucket.period][bucket.stack][k];
    }
    expr -= static_cast<double>(bucket.capacity);

    result += penalty * squared_penalty(expr);
  }

  return result;
}

// Assembles the full QUBO objective and installs it on qmodel.
//
// Energy = sum of the costs of the selected sequences
//        + M_b * (1 - sum of block b's sequences)^2      for every block b
//        + M   * (conflict and capacity penalties)
//
// The multipliers are the smallest ones that still leave every infeasible
// assignment strictly worse than the model's optimum. Write S for the sum over
// blocks of the cheapest sequence cost (a lower bound on the optimum) and U for
// an upper bound on it. An assignment that leaves a set K of blocks unassigned
// and violates v other constraints has energy at least
//
//     (S - sum_{b in K} min_b) + sum_{b in K} M_b + v * M,
//
// so M = U - S + 1 and M_b = U - S + min_b + 1 make that at least U + 1 > U
// whenever K or v is non-empty. What matters is the objective *spread* U - S,
// not the size of a single cost: a multiplier taken from the largest sequence
// cost is unrelated to the quantity it has to dominate, so it is both larger
// than needed on most instances and not sufficient in general.
void QUBOModel::update_qubo_objective(const bool verbose)
{
  qmodel->update();
  prune_qubo_variables(verbose);

  int minimumTotal = 0;
  std::vector<int> cheapest(bayState.numberOfBlocks, 0);
  for (const auto &bb : bayState.blockingBlock)
  {
    int best = -1;
    for (const auto &seq : sequence[bb.priority])
    {
      if (qubo_active(seq) && (best < 0 || seq.length() < best))
      {
        best = seq.length();
      }
    }
    cheapest[bb.priority] = std::max(best, 0);
    minimumTotal += cheapest[bb.priority];
  }

  const double spread = std::max(qubo_upper_bound() - minimumTotal, 0);
  const double penalty = spread + 1.0;

  GRBQuadExpr qObjExpr(0.0);
  GRBQuadExpr qAssignExpr(0.0);
  for (const auto &bb : bayState.blockingBlock)
  {
    GRBLinExpr assignment(1.0);
    for (const auto &seq : sequence[bb.priority])
    {
      if (!qubo_active(seq))
      {
        continue;
      }
      qObjExpr += static_cast<double>(seq.length()) * (*(seq.qvariable));
      assignment -= *(seq.qvariable);
    }
    qAssignExpr += (spread + cheapest[bb.priority] + 1.0) * squared_penalty(assignment);
  }

  ForbiddenPairs pairs;
  collect_conflict_pairs(pairs);
  GRBQuadExpr qPairExpr = build_capacity_penalty(penalty, pairs);
  for (const auto &pair : pairs)
  {
    qPairExpr.addTerm(penalty, pair.second.first, pair.second.second);
  }

  GRBQuadExpr qTotalObjExpr(0.0);
  qTotalObjExpr += qObjExpr + qAssignExpr + qPairExpr;
  quboOffset = qTotalObjExpr.getLinExpr().getConstant();
  qmodel->setObjective(qTotalObjExpr);
  qmodel->update();

  // sequences ruled out by cost take no part in the QUBO at all
  for (const auto &bb : bayState.blockingBlock)
  {
    for (const auto &seq : sequence[bb.priority])
    {
      if (seq.type != Inactive)
      {
        seq.qvariable->set(GRB_DoubleAttr_UB,
                           quboPruned[bb.priority][seq.no] ? 0.0 : 1.0);
      }
    }
  }
  qmodel->update();

  if (verbose)
  {
    std::cout << qmodel->get(GRB_IntAttr_NumVars) << " QUBO variables:" << std::endl;
    GRBVar *qvars = qmodel->getVars();
    for (int i = 0; i < qmodel->get(GRB_IntAttr_NumVars); ++i)
    {
      std::cout << qvars[i].get(GRB_StringAttr_VarName) << "\t";
    }
    std::cout << std::endl;
    std::cout << "QUBO penalty: " << penalty << " (constraints), "
              << "assignment " << (spread + 1.0) << "..."
              << (spread + 1.0 + *std::max_element(cheapest.begin(), cheapest.end()))
              << std::endl;

    std::cout << "QUBO objective: "
              << qmodel->getObjective().size() + qmodel->getObjective().getLinExpr().size()
              << " terms" << std::endl;
    std::cout << qmodel->getObjective() << std::endl;
  }
}

// Reports the QUBO's own optimum next to the IP's, so the two can be compared:
// they must agree whenever the lower-bound cut is not binding. This is the
// standing check that the penalty weights are still large enough.
void QUBOModel::report_qubo(const char *phase) const
{
  if (qmodel->get(GRB_IntAttr_Status) != GRB_OPTIMAL)
  {
    std::cerr << "qubo_" << phase << "=nonoptimal" << std::endl;
    return;
  }
  std::cerr << "qubo_" << phase << "_objective=" << qmodel->get(GRB_DoubleAttr_ObjVal);
  if (model->get(GRB_IntAttr_Status) == GRB_OPTIMAL)
  {
    std::cerr << " ip_objective=" << model->get(GRB_DoubleAttr_ObjVal);
  }
  std::cerr << " qubo_vars=" << qmodel->get(GRB_IntAttr_NumVars) << std::endl;
}

void QUBOModel::print_ip_debug() const
{
  std::cout << model->get(GRB_IntAttr_NumVars) << " IP variables:" << std::endl;
  GRBVar *vars = model->getVars();
  for (int i = 0; i < model->get(GRB_IntAttr_NumVars); ++i)
  {
    std::cout << vars[i].get(GRB_StringAttr_VarName) << "\t";
  }
  std::cout << std::endl;

  std::cout << model->get(GRB_IntAttr_NumConstrs) << " IP constraints:" << std::endl;
  GRBConstr *constrs = model->getConstrs();
  for (int i = 0; i < model->get(GRB_IntAttr_NumConstrs); ++i)
  {
    printf("%d: %s %c %g\n", i + 1, constrs[i].get(GRB_StringAttr_ConstrName).c_str(),
           constrs[i].get(GRB_CharAttr_Sense), constrs[i].get(GRB_DoubleAttr_RHS));
    GRBLinExpr lhs = model->getRow(constrs[i]);
    for (unsigned int j = 0; j < lhs.size(); ++j)
    {
      std::cout << " + " << lhs.getCoeff(j) << "*" << lhs.getVar(j).get(GRB_StringAttr_VarName);
    }
    std::cout << std::endl;
  }

  std::cout << "IP objective: " << std::endl;
  std::cout << model->getObjective() << std::endl;
}

// Snapshots qmodel's final objective into quboVariableNames/quboCoefficients.
// Must run before qmodel is deleted -- called from solve()'s common cleanup
// path, right before "delete qm;". export_qubo() only ever reads this
// snapshot, so it stays valid (and callable) after solve() has returned and
// torn down its live Gurobi objects.
void QUBOModel::capture_qubo()
{
  qmodel->update();

  const int n = qmodel->get(GRB_IntAttr_NumVars);
  GRBVar *vars = qmodel->getVars();

  quboVariableNames.resize(static_cast<std::size_t>(n));
  for (int i = 0; i < n; ++i)
  {
    quboVariableNames[static_cast<std::size_t>(i)] = vars[i].get(GRB_StringAttr_VarName);
  }

  quboVariablePriority.assign(static_cast<std::size_t>(n), -1);
  quboVariableCost.assign(static_cast<std::size_t>(n), 0);
  quboVariableRelocations.assign(static_cast<std::size_t>(n), std::vector<Relocation>());
  for (const auto &bb : bayState.blockingBlock)
  {
    for (const auto &seq : sequence[bb.priority])
    {
      if (seq.qvariable == nullptr)
      {
        continue;
      }
      const int idx = seq.qvariable->index();
      if (idx < 0 || idx >= n)
      {
        continue;
      }
      quboVariablePriority[static_cast<std::size_t>(idx)] = bb.priority;
      quboVariableCost[static_cast<std::size_t>(idx)] = seq.length();
      quboVariableRelocations[static_cast<std::size_t>(idx)]
          .assign(seq.relocations.begin(), seq.relocations.end());
    }
  }

  quboCoefficients.clear();

  const GRBQuadExpr obj = qmodel->getObjective();
  const GRBLinExpr lin = obj.getLinExpr();

  quboOffset = lin.getConstant();

  for (unsigned int i = 0; i < lin.size(); ++i)
  {
    const int idx = lin.getVar(i).index();
    quboCoefficients[std::make_pair(idx, idx)] += lin.getCoeff(i);
  }

  for (unsigned int i = 0; i < obj.size(); ++i)
  {
    const int i1 = obj.getVar1(i).index();
    const int i2 = obj.getVar2(i).index();
    const double c = obj.getCoeff(i);

    if (i1 == i2)
    {
      quboCoefficients[std::make_pair(i1, i1)] += c;
    }
    else
    {
      quboCoefficients[std::make_pair(std::min(i1, i2), std::max(i1, i2))] += c;
    }
  }

  // Drop every column that carries no coefficient at all -- a sequence ruled out
  // by cost, or a slack variable whose bucket turned out to be redundant. Such a
  // column cannot change the energy, so keeping it would only widen the model.
  // What is left is renumbered 0..n-1 so the exported file is compact.
  std::vector<int> remap(quboVariableNames.size(), -1);
  int kept = 0;
  for (const auto &entry : quboCoefficients)
  {
    if (entry.second == 0.0)
    {
      continue;
    }
    for (const int idx : {entry.first.first, entry.first.second})
    {
      if (idx >= 0 && idx < static_cast<int>(remap.size()) && remap[idx] < 0)
      {
        remap[idx] = 0;
      }
    }
  }
  for (std::size_t i = 0; i < remap.size(); ++i)
  {
    if (remap[i] == 0)
    {
      remap[i] = kept++;
    }
  }

  if (kept < static_cast<int>(quboVariableNames.size()))
  {
    std::vector<std::string> names(static_cast<std::size_t>(kept));
    std::vector<int> priority(static_cast<std::size_t>(kept), -1);
    std::vector<int> cost(static_cast<std::size_t>(kept), 0);
    std::vector<std::vector<Relocation>> relocations(static_cast<std::size_t>(kept));
    for (std::size_t i = 0; i < remap.size(); ++i)
    {
      if (remap[i] < 0)
      {
        continue;
      }
      const std::size_t to = static_cast<std::size_t>(remap[i]);
      names[to] = quboVariableNames[i];
      priority[to] = quboVariablePriority[i];
      cost[to] = quboVariableCost[i];
      relocations[to] = quboVariableRelocations[i];
    }
    quboVariableNames.swap(names);
    quboVariablePriority.swap(priority);
    quboVariableCost.swap(cost);
    quboVariableRelocations.swap(relocations);

    std::map<std::pair<int, int>, double> compacted;
    for (const auto &entry : quboCoefficients)
    {
      if (entry.second == 0.0)
      {
        continue;
      }
      const int i = remap[static_cast<std::size_t>(entry.first.first)];
      const int j = remap[static_cast<std::size_t>(entry.first.second)];
      compacted[std::make_pair(std::min(i, j), std::max(i, j))] += entry.second;
    }
    quboCoefficients.swap(compacted);
  }

  quboCaptured = true;
}

void QUBOModel::print_solution(std::ostream &os) const
{
  // solutionUB is only filled in when the branch-and-bound loop breaks via
  // its "found a solutionUB this iteration" paths; when the initial greedy
  // upper bound already matches the LP lower bound, the loop can finish
  // without ever touching it, leaving it at its -1 sentinel. Fall back to
  // the plain LB-iteration assignment (solution) in that case: once
  // lowerBound == upperBound, that assignment's total cost equals the
  // proven optimum, so it IS the optimal IP solution -- the same one the
  // greedy upper bound happened to match -- even if some of its sequences
  // are still "Blocking" placeholders rather than fully expanded paths.
  // Only trust it once its cost is checked against the reported optimum,
  // since on an unresolved (e.g. time-limited) run the last LB iteration
  // may not correspond to the greedy solution actually reported.
  bool solutionUBValid = true;
  for (const auto &bb : bayState.blockingBlock)
  {
    if (solutionUB[bb.priority] < 0 ||
        solutionUB[bb.priority] >= static_cast<int>(sequence[bb.priority].size()))
    {
      solutionUBValid = false;
      break;
    }
  }

  const std::vector<int> *chosen = nullptr;
  if (solutionUBValid)
  {
    chosen = &solutionUB;
  }
  else
  {
    int total = 0;
    bool valid = true;
    for (const auto &bb : bayState.blockingBlock)
    {
      const int sq = solution[bb.priority];
      if (sq < 0 || sq >= static_cast<int>(sequence[bb.priority].size()))
      {
        valid = false;
        break;
      }
      total += sequence[bb.priority][sq].length();
    }
    if (valid && total == upper_bound())
    {
      chosen = &solution;
    }
  }

  if (chosen == nullptr)
  {
    os << "IP variables in the optimal solution: not available here "
          "(no IP model assignment matching the reported optimum was "
          "retained)" << std::endl;
    return;
  }

  os << bayState.blockingBlock.size() << " IP variables in the optimal solution:" << std::endl;

  int totalCost = 0;
  for (const auto &bb : bayState.blockingBlock)
  {
    const int sq = (*chosen)[bb.priority];
    const Sequence &seq = sequence[bb.priority][sq];
    const int cost = seq.length();

    os << "  x(" << bb.priority << "," << sq << ") = 1, cost = " << cost << std::endl;
    totalCost += cost;
  }

  os << "Cost of the optimal solution: " << totalCost << std::endl;
}

// Dumps the QUBO objective captured by capture_qubo() as a plain-text,
// dependency-free triplet format: "<i> <j> <coefficient>" per line, i<=j,
// diagonal = linear bias. Directly loadable as a dimod QUBO dict, e.g. via
// the bundled load_qubo.py. Must be called after solve().
bool QUBOModel::export_qubo(const std::string &filename) const
{
  if (!quboCaptured)
  {
    std::cerr << "QUBOModel::export_qubo: solve() must run first" << std::endl;
    return false;
  }

  std::ofstream ofs(filename);
  if (!ofs)
  {
    return false;
  }

  ofs << "# QUBO exported from RBRP QUBOModel (Tanaka-Voss restricted BRP)\n";
  ofs << "# load with: dimod.BinaryQuadraticModel.from_qubo(Q) -- see load_qubo.py\n";
  ofs << "# variables: " << quboVariableNames.size() << "\n";
  // Energy(file) + offset = the model's objective value, the penalty constants
  // being the part a bare coefficient list cannot carry.
  ofs << "# offset " << quboOffset << "\n";
  for (std::size_t i = 0; i < quboVariableNames.size(); ++i)
  {
    ofs << "# var " << i << " " << quboVariableNames[i] << "\n";
  }
  // "# cost <i> <n>": selecting variable i costs n relocations.
  // "# reloc <i> <period> <src> <dst>": one line per relocation in
  // variable i's path, in order. Together these let a downstream tool
  // (see verify_solution.py) rebuild and print the relocation diagram
  // for any feasible combination of selected variables, including a
  // bitstring sampled on a quantum computer, without needing Gurobi.
  for (std::size_t i = 0; i < quboVariableNames.size(); ++i)
  {
    if (quboVariablePriority[i] < 0)
    {
      continue;
    }
    ofs << "# cost " << i << " " << quboVariableCost[i] << "\n";
    for (const auto &r : quboVariableRelocations[i])
    {
      ofs << "# reloc " << i << " " << r.period << " " << r.src << " " << r.dst << "\n";
    }
  }
  for (const auto &entry : quboCoefficients)
  {
    ofs << entry.first.first << "\t" << entry.first.second << "\t" << entry.second << "\n";
  }

  return ofs.good();
}
