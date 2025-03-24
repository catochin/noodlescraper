document.addEventListener('DOMContentLoaded', function() {
    // Network graph animation in background
    const canvas = document.getElementById('networkGraph');
    if (canvas) {
        initNetworkGraph(canvas);
    }
    
    // Handle video card clicks to store data in localStorage
    document.querySelectorAll('.video-card').forEach(card => {
        card.addEventListener('click', function(e) {
            e.preventDefault();
            
            const videoData = JSON.parse(this.getAttribute('data-video'));
            localStorage.setItem('currentVideo', JSON.stringify(videoData));
            
            // Navigate to the video detail page
            window.location.href = this.getAttribute('href');
        });
    });
    
    // Check if we're on the video detail page
    if (document.querySelector('.video-detail')) {
        loadVideoFromLocalStorage();
    }
    
    // Quality selector
    setupQualityButtons();
});

function loadVideoFromLocalStorage() {
    const videoData = JSON.parse(localStorage.getItem('currentVideo') || '{}');
    
    // Update page title
    if (videoData.title) {
        document.title = videoData.title + ' - NoodleScraper';
        document.querySelector('.video-header h1').textContent = videoData.title;
    }
    
    // Update tags
    if (videoData.tags && videoData.tags.length > 0) {
        const tagsContainer = document.querySelector('.video-header .tags');
        if (tagsContainer) {
            tagsContainer.innerHTML = '';
            videoData.tags.forEach(tag => {
                const tagEl = document.createElement('span');
                tagEl.className = 'tag';
                tagEl.textContent = tag;
                tagsContainer.appendChild(tagEl);
            });
        }
    }
    
    // Create quality buttons
    if (videoData.sources && videoData.sources.length > 0) {
        const qualityButtonsContainer = document.querySelector('.quality-buttons');
        if (qualityButtonsContainer) {
            qualityButtonsContainer.innerHTML = '';
            
            videoData.sources.forEach(source => {
                const button = document.createElement('button');
                button.className = 'quality-button' + (source.default ? ' active' : '');
                button.setAttribute('data-url', source.file);
                button.setAttribute('data-quality', source.label);
                button.textContent = source.label + 'p';
                
                qualityButtonsContainer.appendChild(button);
            });
            
            // Setup quality button events
            setupQualityButtons();
        }
    }
}

function setupQualityButtons() {
    const qualityButtons = document.querySelectorAll('.quality-button');
    if (qualityButtons.length > 0) {
        qualityButtons.forEach(button => {
            button.addEventListener('click', function() {
                // Remove active class from all buttons
                qualityButtons.forEach(btn => btn.classList.remove('active'));
                
                // Add active class to clicked button
                this.classList.add('active');
                
                // Update video source with the selected quality
                const videoPlayer = document.getElementById('videoPlayer');
                const videoUrl = this.getAttribute('data-url');
                
                if (videoPlayer && videoUrl) {
                    videoPlayer.src = videoUrl;
                    videoPlayer.play();
                }
            });
        });
        
        // Activate the first quality button by default if none is active
        if (!document.querySelector('.quality-button.active') && qualityButtons.length > 0) {
            qualityButtons[0].click();
        }
    }
}

// Network graph animation
function initNetworkGraph(canvas) {
    const ctx = canvas.getContext('2d');
    const width = window.innerWidth;
    const height = window.innerHeight;
    
    // Set canvas dimensions to match window
    canvas.width = width;
    canvas.height = height;
    
    // Create nodes
    const nodeCount = 30;
    const nodes = [];
    
    for (let i = 0; i < nodeCount; i++) {
        nodes.push({
            x: Math.random() * width,
            y: Math.random() * height,
            radius: Math.random() * 2 + 1,
            vx: Math.random() * 2 - 1,
            vy: Math.random() * 2 - 1
        });
    }
    
    // Animation loop
    function animate() {
        ctx.clearRect(0, 0, width, height);
        
        // Update and draw connections
        ctx.strokeStyle = '#333333';
        ctx.lineWidth = 0.5;
        
        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                const node1 = nodes[i];
                const node2 = nodes[j];
                
                const dx = node1.x - node2.x;
                const dy = node1.y - node2.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                
                if (distance < 200) {
                    ctx.beginPath();
                    ctx.moveTo(node1.x, node1.y);
                    ctx.lineTo(node2.x, node2.y);
                    ctx.stroke();
                }
            }
        }
        
        // Update and draw nodes
        ctx.fillStyle = '#555555';
        
        nodes.forEach(node => {
            // Update position
            node.x += node.vx;
            node.y += node.vy;
            
            // Bounce off edges
            if (node.x < 0 || node.x > width) node.vx *= -1;
            if (node.y < 0 || node.y > height) node.vy *= -1;
            
            // Draw node
            ctx.beginPath();
            ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
            ctx.fill();
        });
        
        requestAnimationFrame(animate);
    }
    
    animate();
    
    // Resize canvas when window is resized
    window.addEventListener('resize', function() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    });
}
