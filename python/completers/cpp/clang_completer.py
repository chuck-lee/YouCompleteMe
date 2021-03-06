#!/usr/bin/env python
#
# Copyright (C) 2011, 2012  Strahinja Val Markovic  <val@markovic.io>
#
# This file is part of YouCompleteMe.
#
# YouCompleteMe is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# YouCompleteMe is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with YouCompleteMe.  If not, see <http://www.gnu.org/licenses/>.

from completers.completer import Completer
from collections import defaultdict
import vim
import vimsupport
import ycm_core
from flags import Flags

CLANG_FILETYPES = set( [ 'c', 'cpp', 'objc', 'objcpp' ] )
MAX_DIAGNOSTICS_TO_DISPLAY = int( vimsupport.GetVariableValue(
  "g:ycm_max_diagnostics_to_display" ) )


class ClangCompleter( Completer ):
  def __init__( self ):
    self.completer = ycm_core.ClangCompleter()
    self.completer.EnableThreading()
    self.contents_holder = []
    self.filename_holder = []
    self.last_prepared_diagnostics = []
    self.parse_future = None
    self.flags = Flags()


  def SupportedFiletypes( self ):
    return CLANG_FILETYPES


  def GetUnsavedFilesVector( self ):
    # CAREFUL HERE! For UnsavedFile filename and contents we are referring
    # directly to Python-allocated and -managed memory since we are accepting
    # pointers to data members of python objects. We need to ensure that those
    # objects outlive our UnsavedFile objects. This is why we need the
    # contents_holder and filename_holder lists, to make sure the string objects
    # are still around when we call CandidatesForQueryAndLocationInFile.  We do
    # this to avoid an extra copy of the entire file contents.

    files = ycm_core.UnsavedFileVec()
    self.contents_holder = []
    self.filename_holder = []
    for buffer in vimsupport.GetUnsavedBuffers():
      if not ClangAvailableForBuffer( buffer ):
        continue
      contents = '\n'.join( buffer )
      name = buffer.name
      if not contents or not name:
        continue
      self.contents_holder.append( contents )
      self.filename_holder.append( name )

      unsaved_file = ycm_core.UnsavedFile()
      unsaved_file.contents_ = self.contents_holder[ -1 ]
      unsaved_file.length_ = len( self.contents_holder[ -1 ] )
      unsaved_file.filename_ = self.filename_holder[ -1 ]

      files.append( unsaved_file )

    return files


  def CandidatesForQueryAsync( self, query ):
    filename = vim.current.buffer.name

    if self.completer.UpdatingTranslationUnit( filename ):
      vimsupport.PostVimMessage( 'Still parsing file, no completions yet.' )
      self.completions_future = None
      return

    flags = self.flags.FlagsForFile( filename )
    if not flags:
      vimsupport.PostVimMessage( 'Still no compile flags, no completions yet.' )
      self.completions_future = None
      return

    # TODO: sanitize query, probably in C++ code

    files = ycm_core.UnsavedFileVec()
    if not query:
      files = self.GetUnsavedFilesVector()

    line, _ = vim.current.window.cursor
    column = int( vim.eval( "s:completion_start_column" ) ) + 1
    self.completions_future = (
      self.completer.CandidatesForQueryAndLocationInFileAsync(
        query,
        filename,
        line,
        column,
        files,
        flags ) )


  def CandidatesFromStoredRequest( self ):
    if not self.completions_future:
      return []
    results = [ CompletionDataToDict( x ) for x in
                self.completions_future.GetResults() ]
    if not results:
      vimsupport.PostVimMessage( 'No completions found; errors in the file?' )
    return results


  def OnFileReadyToParse( self ):
    if vimsupport.NumLinesInBuffer( vim.current.buffer ) < 5:
      self.parse_future = None
      return

    filename = vim.current.buffer.name
    if self.completer.UpdatingTranslationUnit( filename ):
      return

    flags = self.flags.FlagsForFile( filename )
    if not flags:
      self.parse_future = None
      return

    self.parse_future = self.completer.UpdateTranslationUnitAsync(
      filename,
      self.GetUnsavedFilesVector(),
      flags )


  def DiagnosticsForCurrentFileReady( self ):
    if not self.parse_future:
      return False

    return self.parse_future.ResultsReady()


  def GetDiagnosticsForCurrentFile( self ):
    if self.DiagnosticsForCurrentFileReady():
      diagnostics = self.completer.DiagnosticsForFile( vim.current.buffer.name )
      self.diagnostic_store = DiagnosticsToDiagStructure( diagnostics )
      self.last_prepared_diagnostics = [ DiagnosticToDict( x ) for x in
          diagnostics[ : MAX_DIAGNOSTICS_TO_DISPLAY ] ]
      self.parse_future = None
    return self.last_prepared_diagnostics


  def ShowDetailedDiagnostic( self ):
    current_line, current_column = vimsupport.CurrentLineAndColumn()

    # CurrentLineAndColumn() numbers are 0-based, clang numbers are 1-based
    current_line += 1
    current_column += 1

    current_file = vim.current.buffer.name
    diagnostics = self.diagnostic_store[ current_file ][ current_line ]

    if not diagnostics:
      vimsupport.PostVimMessage( "No diagnostic for current line!" )
      return

    closest_diagnostic = None
    distance_to_closest_diagnostic = 999

    for diagnostic in diagnostics:
      distance = abs( current_column - diagnostic.column_number_ )
      if distance < distance_to_closest_diagnostic:
        distance_to_closest_diagnostic = distance
        closest_diagnostic = diagnostic

    vimsupport.EchoText( closest_diagnostic.long_formatted_text_ )


  def ShouldUseNow( self, start_column ):
    return ShouldUseClang( start_column )


  def DebugInfo( self ):
    filename = vim.current.buffer.name
    flags = self.flags.FlagsForFile( filename ) or []
    return 'Flags for {0}:\n{1}'.format( filename, list( flags ) )


# TODO: make these functions module-local
def CompletionDataToDict( completion_data ):
  # see :h complete-items for a description of the dictionary fields
  return {
    'word' : completion_data.TextToInsertInBuffer(),
    'abbr' : completion_data.MainCompletionText(),
    'menu' : completion_data.ExtraMenuInfo(),
    'kind' : completion_data.kind_,
    'info' : completion_data.DetailedInfoForPreviewWindow(),
    'dup'  : 1,
  }


def DiagnosticToDict( diagnostic ):
  # see :h getqflist for a description of the dictionary fields
  return {
    # TODO: wrap the bufnr generation into a function
    'bufnr' : int( vim.eval( "bufnr('{0}', 1)".format(
      diagnostic.filename_ ) ) ),
    'lnum'  : diagnostic.line_number_,
    'col'   : diagnostic.column_number_,
    'text'  : diagnostic.text_,
    'type'  : diagnostic.kind_,
    'valid' : 1
  }


def DiagnosticsToDiagStructure( diagnostics ):
  structure = defaultdict(lambda : defaultdict(list))
  for diagnostic in diagnostics:
    structure[ diagnostic.filename_ ][ diagnostic.line_number_ ].append(
        diagnostic )
  return structure


def ClangAvailableForBuffer( buffer_object ):
  filetype = vim.eval( 'getbufvar({0}, "&ft")'.format( buffer_object.number ) )
  return filetype in CLANG_FILETYPES


def ShouldUseClang( start_column ):
  line = vim.current.line
  previous_char_index = start_column - 1
  if ( not len( line ) or
       previous_char_index < 0 or
       previous_char_index >= len( line ) ):
    return False

  if line[ previous_char_index ] == '.':
    return True

  if previous_char_index - 1 < 0:
    return False

  two_previous_chars = line[ previous_char_index - 1 : start_column ]
  if ( two_previous_chars == '->' or two_previous_chars == '::' ):
    return True

  return False
