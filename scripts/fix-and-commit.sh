#!/bin/bash
claude -p "Review all modified Python files for syntax errors and import issues. Fix any found. Then run the app to verify it starts. If all good, git add and commit with a descriptive message." \
  --allowedTools "Read,Edit,Bash,Write"
