# Generates the Steam library artwork for the PlayVault tile (run on Windows).
#   powershell -ExecutionPolicy Bypass -File tools\make_art.ps1
Add-Type -AssemblyName System.Drawing
$out = Join-Path (Split-Path -Parent $PSScriptRoot) 'app\art'
New-Item -ItemType Directory -Force $out | Out-Null

$accent = [System.Drawing.Color]::FromArgb(255, 79, 140, 255)
$violet = [System.Drawing.Color]::FromArgb(255, 139, 92, 246)
$bg1 = [System.Drawing.Color]::FromArgb(255, 10, 13, 19)
$bg2 = [System.Drawing.Color]::FromArgb(255, 22, 30, 48)

function New-Canvas($w, $h, [bool]$transparent = $false) {
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'; $g.InterpolationMode = 'HighQualityBicubic'
  if (-not $transparent) {
    $rect = New-Object System.Drawing.Rectangle 0, 0, $w, $h
    $lg = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, $bg2, $bg1, 65
    $g.FillRectangle($lg, $rect)
    # soft glows
    foreach ($spec in @(@(0.15, 0.2, $accent), @(0.85, 0.85, $violet))) {
      $cx = $w * $spec[0]; $cy = $h * $spec[1]; $r = [Math]::Max($w, $h) * 0.55
      $path = New-Object System.Drawing.Drawing2D.GraphicsPath
      $path.AddEllipse($cx - $r, $cy - $r, 2 * $r, 2 * $r)
      $pgb = New-Object System.Drawing.Drawing2D.PathGradientBrush $path
      $c = $spec[2]
      $pgb.CenterColor = [System.Drawing.Color]::FromArgb(70, $c.R, $c.G, $c.B)
      $pgb.SurroundColors = @([System.Drawing.Color]::FromArgb(0, $c.R, $c.G, $c.B))
      $g.FillPath($pgb, $path)
    }
  }
  return @($bmp, $g)
}

function Draw-Mark($g, $cx, $cy, $size) {
  # rounded square badge with a vault dial
  $s = $size; $x = $cx - $s / 2; $y = $cy - $s / 2; $rad = $s * 0.26
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $path.AddArc($x, $y, $rad, $rad, 180, 90); $path.AddArc($x + $s - $rad, $y, $rad, $rad, 270, 90)
  $path.AddArc($x + $s - $rad, $y + $s - $rad, $rad, $rad, 0, 90); $path.AddArc($x, $y + $s - $rad, $rad, $rad, 90, 90)
  $path.CloseFigure()
  $rect = New-Object System.Drawing.RectangleF $x, $y, $s, $s
  $lg = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, $accent, $violet, 45
  $g.FillPath($lg, $path)
  $white = [System.Drawing.Color]::FromArgb(245, 255, 255, 255)
  $pen = New-Object System.Drawing.Pen $white, ($s * 0.07)
  $r = $s * 0.28
  $g.DrawEllipse($pen, $cx - $r, $cy - $r, 2 * $r, 2 * $r)
  for ($i = 0; $i -lt 8; $i++) {
    $a = [Math]::PI * 2 * $i / 8
    $g.DrawLine($pen, $cx + [Math]::Cos($a) * $r * 0.35, $cy + [Math]::Sin($a) * $r * 0.35, $cx + [Math]::Cos($a) * $r * 0.62, $cy + [Math]::Sin($a) * $r * 0.62)
  }
  $hub = $s * 0.08
  $g.FillEllipse((New-Object System.Drawing.SolidBrush $white), $cx - $hub, $cy - $hub, 2 * $hub, 2 * $hub)
}

function Draw-Text($g, $text, $cx, $cy, $px, [string]$align = 'center', $alpha = 255) {
  $font = New-Object System.Drawing.Font 'Segoe UI', $px, ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
  $fmt = New-Object System.Drawing.StringFormat
  $fmt.Alignment = if ($align -eq 'left') { 'Near' } else { 'Center' }
  $fmt.LineAlignment = 'Center'
  $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($alpha, 255, 255, 255))
  $g.DrawString($text, $font, $brush, [single]$cx, [single]$cy, $fmt)
}

function Save($pair, $name) { $pair[0].Save((Join-Path $out $name), [System.Drawing.Imaging.ImageFormat]::Png); $pair[1].Dispose(); $pair[0].Dispose() }

$dot = [char]0x00B7
$tagline = "EPIC  $dot  GOG  $dot  ONE LIBRARY"
# portrait 600x900
$c = New-Canvas 600 900; Draw-Mark $c[1] 300 360 230; Draw-Text $c[1] 'PlayVault' 300 560 74; Draw-Text $c[1] $tagline 300 625 22 'center' 170; Save $c 'portrait.png'
# wide 920x430
$c = New-Canvas 920 430; Draw-Mark $c[1] 230 215 190; Draw-Text $c[1] 'PlayVault' 360 195 92 'left'; Draw-Text $c[1] $tagline 366 268 22 'left' 170; Save $c 'wide.png'
# hero 1920x620
$c = New-Canvas 1920 620; Draw-Mark $c[1] 1560 310 300; Save $c 'hero.png'
# logo 1200x360 transparent
$c = New-Canvas 1200 360 $true; Draw-Mark $c[1] 170 180 240; Draw-Text $c[1] 'PlayVault' 330 180 140 'left'; Save $c 'logo.png'
# icon 256x256 transparent
$c = New-Canvas 256 256 $true; Draw-Mark $c[1] 128 128 240; Save $c 'icon.png'
Get-ChildItem $out | Select-Object Name, Length
