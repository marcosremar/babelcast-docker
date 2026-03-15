#!/bin/bash

echo "🔍 Testing FFmpeg performance in preprod environment..."

# Test 1: Basic FFmpeg info
echo "📊 FFmpeg version and capabilities:"
ffmpeg -version | head -5

# Test 2: CPU info
echo "🖥️ CPU information:"
nproc
lscpu | grep "Model name" | head -1

# Test 3: Memory info
echo "💾 Memory information:"
free -h

# Test 4: Test encoding performance without display (simulation)
echo "🎬 Testing encoding performance (simulation)..."
echo "Creating a test video from a solid color..."

# Create a test video using color source instead of x11grab
ffmpeg -f lavfi -i "color=c=black:size=1280x720:duration=10" \
  -f lavfi -i "sine=frequency=1000:duration=10" \
  -c:v libx264 -preset ultrafast -crf 32 \
  -profile:v baseline -level 3.0 -pix_fmt yuv420p \
  -threads 1 -tune zerolatency -g 30 -keyint_min 30 \
  -bf 0 -refs 1 \
  -c:a aac -b:a 128k \
  -t 10 -y test_recording.mp4 2>&1 | grep -E "(frame=|fps=|speed=|error)"

echo "✅ Test completed. Analyzing results..."

# Analyze the generated video
echo "🔍 Analyzing the generated video..."
ffprobe -v quiet -show_entries stream=codec_name,profile,level,has_b_frames,refs,bit_rate -of csv test_recording.mp4

echo "📊 Video analysis:"
echo "=================="

# Check if B-frames are disabled
if ffprobe -v quiet -show_entries stream=has_b_frames -of csv test_recording.mp4 | grep -q ",0"; then
    echo "✅ B-frames successfully disabled"
else
    echo "❌ B-frames still present"
fi

# Check profile
if ffprobe -v quiet -show_entries stream=profile -of csv test_recording.mp4 | grep -q "baseline"; then
    echo "✅ Baseline profile applied"
else
    echo "❌ Wrong profile detected"
fi

# Check level
if ffprobe -v quiet -show_entries stream=level -of csv test_recording.mp4 | grep -q "30"; then
    echo "✅ Level 3.0 applied"
else
    echo "❌ Wrong level detected"
fi

# Check file size
size=$(stat -f%z test_recording.mp4 2>/dev/null || stat -c%s test_recording.mp4 2>/dev/null)
size_mb=$(echo "scale=2; $size / 1024 / 1024" | bc -l 2>/dev/null || echo "unknown")
echo "📁 File size: ${size_mb}MB"

# Cleanup
rm -f test_recording.mp4 