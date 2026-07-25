#!/usr/bin/python3
"""Wrapper to run find_key_enhanced.py with proper LLDB Python path."""
import sys
sys.path.insert(0, '/Library/Developer/CommandLineTools/Library/PrivateFrameworks/LLDB.framework/Resources/Python')

# Now load and run the enhanced scanner
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
exec(compile(open(os.path.join(script_dir, 'find_key_enhanced.py')).read(),
             os.path.join(script_dir, 'find_key_enhanced.py'), 'exec'))
