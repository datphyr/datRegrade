REM Example: a full-matrix datRegrade run on Windows.
REM
REM Prepares REGRADES/T1/ for a Terminator HDR-to-SDR regrade, using a
REM datMatcher build kept outside this repository.
REM
REM This only *prepares* the project; nothing is executed here.

python auto_regrade.py ^
  --source "D:\Garbage\REGRADING\T1\SRC\TERMINATOR [40TH ANNIVERSARY REMASTER] (1984).mkv" ^
  --target "D:\Garbage\REGRADING\T1\SRC\Terminator.1984.BDRemux.1080p.mpeg2.mkv" ^
  --source-crop "0,42,0,-42" ^
  --source-trim "5727,148502" ^
  --target-crop "0,20,-2,-22" ^
  --target-trim "5535,148310" ^
  --frames "1340,11337,14451,16571,16906,22397,44384,66962" ^
  --datmatcher-dir "D:\Garbage\git\datMatcher\build" ^
  --output-dir "T1"

echo.
echo Now run the steps in order:
echo   cd REGRADES\T1 ^&^& 01_index.bat ^&^& 02_extract_source.bat ^&^& 03_match_colors.bat
