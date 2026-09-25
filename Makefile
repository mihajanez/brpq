#
#  $Id: Makefile,v 1.5 2021/02/24 10:18:02 tanaka Exp tanaka $
#  $Revision: 1.5 $
#  $Date: 2021/02/24 10:18:02 $
#  $Author: tanaka $
#
.PHONY: all strip clean depend gui
GUROBI_ROOT = /opt/gurobi1300/linux64
GUROBI_LIBS = -lgurobi_g++8.5 -lgurobi130

OBJS       = bay.o baystate.o greedy.o instance.o bipmodel.o qubomodel.o main.o sequence.o \
	solve.o solution.o
SRCS      := $(OBJS:.o=.cpp)

TARGET     = rbrp_ip

CXX        = g++
MAKEDEP    = g++ -MM

CPPFLAGS   = -I$(GUROBI_ROOT)/include
CXXFLAGS   = -Wall -Wextra -Wconversion -Wold-style-cast -pedantic -O2
# CXXFLAGS  += -march=znver2
# CXXFLAGS  += -march=corei7

LDFLAGS    = 
LIBS       = -L$(GUROBI_ROOT)/lib -lboost_program_options $(GUROBI_LIBS)

override CPPFLAGS += -I. # -DDEBUG -DUSE_CLOCK

all:: $(TARGET)

.cpp.o:
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(DEFS) -c $<

$(TARGET): $(OBJS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(DEFS) -o $@ $(OBJS) $(LDFLAGS) $(LIBS)

strip:: $(TARGET)
	@strip $(TARGET)

gui: $(TARGET)
	@PY=`test -x .venv/bin/python && echo .venv/bin/python || echo python3`; \
	$$PY -m gui.server --open

clean:
	rm -f $(TARGET) $(OBJS) *~ *.bak #*

depend:
	@sed -i -e "/^# START/,/# END/d" Makefile
	@echo "# START" >> Makefile
	@$(MAKEDEP) $(CPPFLAGS) $(DEFS) $(SRCS) >> Makefile
	@echo "# END" >> Makefile

# START
bay.o: bay.cpp bay.hpp instance.hpp
baystate.o: baystate.cpp baystate.hpp bay.hpp instance.hpp
greedy.o: greedy.cpp greedy.hpp bay.hpp instance.hpp solution.hpp \
 sequence.hpp /opt/gurobi1300/linux64/include/gurobi_c++.h \
 /opt/gurobi1300/linux64/include/gurobi_c.h baystate.hpp
instance.o: instance.cpp instance.hpp
bipmodel.o: bipmodel.cpp /opt/gurobi1300/linux64/include/gurobi_c++.h \
 /opt/gurobi1300/linux64/include/gurobi_c.h greedy.hpp bay.hpp \
 instance.hpp solution.hpp sequence.hpp baystate.hpp bipmodel.hpp \
 parameter.hpp
qubomodel.o: qubomodel.cpp /opt/gurobi1300/linux64/include/gurobi_c++.h \
 /opt/gurobi1300/linux64/include/gurobi_c.h greedy.hpp bay.hpp \
 instance.hpp solution.hpp sequence.hpp baystate.hpp qubomodel.hpp \
 parameter.hpp
main.o: main.cpp instance.hpp parameter.hpp solve.hpp
sequence.o: sequence.cpp baystate.hpp bay.hpp instance.hpp sequence.hpp \
 /opt/gurobi1300/linux64/include/gurobi_c++.h \
 /opt/gurobi1300/linux64/include/gurobi_c.h
solve.o: solve.cpp instance.hpp bipmodel.hpp qubomodel.hpp \
 /opt/gurobi1300/linux64/include/gurobi_c++.h \
 /opt/gurobi1300/linux64/include/gurobi_c.h bay.hpp baystate.hpp \
 parameter.hpp sequence.hpp solution.hpp solve.hpp
solution.o: solution.cpp solution.hpp bay.hpp instance.hpp sequence.hpp \
 /opt/gurobi1300/linux64/include/gurobi_c++.h \
 /opt/gurobi1300/linux64/include/gurobi_c.h baystate.hpp
# END
