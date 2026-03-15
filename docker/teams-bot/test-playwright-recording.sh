#!/bin/bash

echo "🧪 Testing Playwright recording with current code configuration..."

# Setup X11 display
export DISPLAY=:99
Xvfb :99 -screen 0 1280x720x24 &
XVFB_PID=$!

# Setup PulseAudio
pulseaudio --start --log-level=4
sleep 2

# Create a simple HTML page with animated content
cat > test-video-page.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Animated Test Page</title>
    <style>
        body { 
            margin: 0;
            padding: 20px;
            background: linear-gradient(45deg, #ff6b6b, #4ecdc4, #45b7d1, #96ceb4);
            background-size: 400% 400%;
            animation: gradientShift 3s ease infinite;
            font-family: Arial, sans-serif;
            color: white;
            overflow: hidden;
        }
        @keyframes gradientShift {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            text-align: center;
        }
        .header {
            font-size: 3em;
            margin: 20px 0;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
        }
        .content-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 30px;
            margin: 40px 0;
        }
        .card {
            background: rgba(255,255,255,0.2);
            border-radius: 15px;
            padding: 30px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.3);
            transition: transform 0.3s ease;
        }
        .card:hover {
            transform: scale(1.05);
        }
        .counter {
            font-size: 4em;
            font-weight: bold;
            margin: 20px 0;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.3);
        }
        .label {
            font-size: 1.5em;
            margin: 10px 0;
        }
        .progress-container {
            width: 100%;
            height: 20px;
            background: rgba(255,255,255,0.3);
            border-radius: 10px;
            overflow: hidden;
            margin: 20px 0;
        }
        .progress-bar {
            height: 100%;
            background: linear-gradient(90deg, #ff6b6b, #4ecdc4);
            border-radius: 10px;
            transition: width 0.5s ease;
        }
        .floating-elements {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 1;
        }
        .floating-element {
            position: absolute;
            width: 30px;
            height: 30px;
            background: rgba(255,255,255,0.7);
            border-radius: 50%;
            animation: float 4s infinite ease-in-out;
        }
        @keyframes float {
            0%, 100% { transform: translateY(0px) rotate(0deg); opacity: 0.7; }
            50% { transform: translateY(-30px) rotate(180deg); opacity: 1; }
        }
        .main-content {
            position: relative;
            z-index: 2;
        }
    </style>
</head>
<body>
    <div class="floating-elements" id="floatingElements"></div>
    
    <div class="main-content">
        <div class="container">
            <div class="header">🎬 Recording Test</div>
            
            <div class="content-grid">
                <div class="card">
                    <div class="counter" id="counter1">0</div>
                    <div class="label">Frames/sec</div>
                    <div class="progress-container">
                        <div class="progress-bar" id="progress1" style="width: 0%"></div>
                    </div>
                </div>
                
                <div class="card">
                    <div class="counter" id="counter2">0</div>
                    <div class="label">Quality Score</div>
                    <div class="progress-container">
                        <div class="progress-bar" id="progress2" style="width: 0%"></div>
                    </div>
                </div>
            </div>
            
            <div style="margin-top: 40px; font-size: 1.5em; opacity: 0.9;">
                Testing video encoding with smooth animations and dynamic content
            </div>
        </div>
    </div>
    
    <script>
        // Create floating elements
        const floatingContainer = document.getElementById('floatingElements');
        for (let i = 0; i < 20; i++) {
            const element = document.createElement('div');
            element.className = 'floating-element';
            element.style.left = Math.random() * 100 + '%';
            element.style.top = Math.random() * 100 + '%';
            element.style.animationDelay = Math.random() * 4 + 's';
            element.style.animationDuration = (Math.random() * 2 + 3) + 's';
            floatingContainer.appendChild(element);
        }
        
        // Update counters and progress bars
        let count1 = 0, count2 = 0;
        
        setInterval(() => {
            count1 += Math.floor(Math.random() * 5) + 1;
            count2 = Math.floor(Math.random() * 100);
            
            document.getElementById('counter1').textContent = count1;
            document.getElementById('counter2').textContent = count2;
            
            document.getElementById('progress1').style.width = Math.min(count1 / 2, 100) + '%';
            document.getElementById('progress2').style.width = count2 + '%';
        }, 150);
    </script>
</body>
</html>
EOF

echo "📄 Created animated test page"

# Start a simple HTTP server
echo "🌐 Starting local server..."
python3 -m http.server 8080 &
SERVER_PID=$!

# Wait for server to start
sleep 3

echo "📹 Starting Playwright recording for 10 seconds..."

# Use Playwright to record the page
npx playwright install chromium

# Create a simple Playwright script
cat > test-recording.js << 'EOF'
const { chromium } = require('playwright');

(async () => {
    const browser = await chromium.launch({
        args: [
            '--use-fake-ui-for-media-stream',
            '--use-fake-device-for-media-stream',
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--autoplay-policy=no-user-gesture-required',
            '--disable-web-security',
            '--disable-features=VizDisplayCompositor'
        ]
    });
    
    const context = await browser.newContext({
        viewport: { width: 1280, height: 720 }
    });
    
    const page = await context.newPage();
    
    // Navigate to our animated test page
    await page.goto('http://localhost:8080/test-video-page.html');
    
    // Wait for page to load and animations to start
    await page.waitForTimeout(3000);
    
    console.log('🎬 Starting recording...');
    
    // Start recording with the same settings as your code
    const ffmpeg = require('child_process').spawn('ffmpeg', [
        '-f', 'x11grab',
        '-s', '1280x720',
        '-i', ':99',
        '-f', 'pulse',
        '-i', 'default',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-profile:v', 'main',
        '-level', '4.0',
        '-pix_fmt', 'yuv420p',
        '-bf', '0',
        '-refs', '1',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-t', '10',
        '-y', 'test-playwright.mp4'
    ]);
    
    // Log FFmpeg output
    ffmpeg.stdout.on('data', (data) => {
        console.log('FFmpeg:', data.toString());
    });
    
    ffmpeg.stderr.on('data', (data) => {
        const output = data.toString();
        if (output.includes('frame=') || output.includes('fps=') || output.includes('speed=')) {
            console.log('FFmpeg:', output.trim());
        }
    });
    
    // Wait for recording to complete
    await new Promise((resolve, reject) => {
        ffmpeg.on('close', (code) => {
            if (code === 0) {
                console.log('✅ Recording completed');
                resolve();
            } else {
                reject(new Error(`FFmpeg exited with code ${code}`));
            }
        });
    });
    
    await browser.close();
})();
EOF

# Run the recording
node test-recording.js

# Stop the server and cleanup
kill $SERVER_PID 2>/dev/null
kill $XVFB_PID 2>/dev/null
pulseaudio --kill 2>/dev/null

echo "🔍 Analyzing the recorded video..."

# Analyze the generated video
ffprobe -v quiet -show_entries stream=codec_name,profile,level,has_b_frames,refs,bit_rate -of csv test-playwright.mp4

echo "📊 Video analysis:"
echo "=================="

# Check if B-frames are disabled
if ffprobe -v quiet -show_entries stream=has_b_frames -of csv test-playwright.mp4 | grep -q ",0"; then
    echo "✅ B-frames successfully disabled"
else
    echo "❌ B-frames still present"
fi

# Check profile
if ffprobe -v quiet -show_entries stream=profile -of csv test-playwright.mp4 | grep -q "Main"; then
    echo "✅ Main profile applied"
else
    echo "❌ Wrong profile detected"
fi

# Check level
if ffprobe -v quiet -show_entries stream=level -of csv test-playwright.mp4 | grep -q "40"; then
    echo "✅ Level 4.0 applied"
else
    echo "❌ Wrong level detected"
fi

# Check file size
size=$(stat -f%z test-playwright.mp4 2>/dev/null || stat -c%s test-playwright.mp4 2>/dev/null)
size_mb=$(echo "scale=2; $size / 1024 / 1024" | bc -l 2>/dev/null || echo "unknown")
echo "📁 File size: ${size_mb}MB"

echo "🎥 Video saved as: test-playwright.mp4"
echo "✅ Test completed! You can now play the video to check quality." 