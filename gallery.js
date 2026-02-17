/**
 * Photo Gallery Component
 * Supports Grid, List, and Fullscreen viewing modes
 */

class PhotoGallery {
    constructor(photos, containerId) {
        this.photos = photos || [];
        this.container = document.getElementById(containerId);
        this.currentView = 'grid'; // grid, list, or fullscreen
        this.currentPhotoIndex = 0;
        if (this.photos.length > 0) {
            this.init();
        }
    }

    static async fromLocal(jsonPath, containerId) {
        const gallery = new PhotoGallery([], containerId);
        const container = document.getElementById(containerId);

        try {
            const response = await fetch(jsonPath);
            if (!response.ok) throw new Error('Local manifest not found');
            const data = await response.json();

            gallery.photos = data.map(photo => ({
                src: photo.src,
                title: photo.title || photo.name.split('.')[0].replace(/-/g, ' '),
                description: photo.description || 'Captured during the Australian journey.'
            }));

            gallery.init();
            return gallery;
        } catch (error) {
            console.error("Local load failed:", error);
            if (container) {
                container.innerHTML = `<p class="error">Wait! We couldn't find the cached images. Please run the sync script.</p>`;
            }
        }
    }

    static async fromOneDrive(shareLink, containerId) {
        // Create an empty gallery first (with loading state potentially)
        const gallery = new PhotoGallery([], containerId);
        const container = document.getElementById(containerId);

        try {
            if (container) container.innerHTML = '<p class="loading" style="text-align:center; padding: 2rem;">Loading from OneDrive...</p>';

            // Step 1: Encode the URL to a share ID
            const base64Value = btoa(shareLink)
                .replace(/\//g, '_')
                .replace(/\+/g, '-')
                .replace(/=/g, '');

            const apiUrl = `https://api.onedrive.com/v1.0/shares/u!${base64Value}/root/children`;

            const response = await fetch(apiUrl);

            if (response.status === 401 || response.status === 403) {
                throw new Error('Access Restricted: OneDrive "Albums" are private by default. Please share the **Folder** instead.');
            }

            if (!response.ok) throw new Error(`OneDrive API Error: ${response.status}`);

            const data = await response.json();

            // Step 2: Map to gallery format
            gallery.photos = (data.value || [])
                .filter(item => item.image || item.file?.mimeType?.startsWith('image/'))
                .map(item => ({
                    src: item["@content.downloadUrl"],
                    title: item.name.split('.')[0].replace(/-/g, ' ').replace(/_/g, ' '),
                    description: `Captured during the Australia journey.`
                }));

            if (gallery.photos.length === 0) {
                throw new Error('No images found in the shared folder.');
            }

            // Step 3: Initialize with new data
            if (container) container.innerHTML = ''; // Clear loading
            gallery.init();
            return gallery;
        } catch (error) {
            console.error("Failed to load OneDrive Album:", error);
            if (container) {
                container.innerHTML = `
                    <div class="gallery-error" style="padding: 3rem; text-align: center; background: var(--bg-secondary); border-radius: 24px; border: 1px solid var(--color-coral); margin: 2rem 0;">
                        <h3 style="color: var(--color-coral); margin-bottom: 1rem;">Gallery Connection Issue</h3>
                        <p style="margin-bottom: 2rem; color: var(--text-primary);">${error.message}</p>
                        <div style="background: var(--bg-card); padding: 2rem; border-radius: 16px; font-size: 0.95rem; text-align: left; box-shadow: var(--shadow-soft);">
                            <strong style="color: var(--color-primary); display: block; margin-bottom: 0.5rem;">How to fix this:</strong>
                            <ol style="padding-left: 1.2rem; line-height: 1.8;">
                                <li>Go to the <strong>Folder</strong> containing the photos (not the Album view).</li>
                                <li>Click <strong>Share</strong> &rarr; <strong>Copy Link</strong>.</li>
                                <li>Ensure it is set to <strong>"Anyone with the link can view"</strong>.</li>
                                <li>Replace the link in your <code>australia.html</code> file.</li>
                            </ol>
                        </div>
                    </div>
                `;
            }
        }
    }

    init() {
        this.renderViewToggle();
        this.renderGallery();
        this.setupKeyboardNavigation();
    }

    renderViewToggle() {
        const toggleHTML = `
            <div class="view-toggle">
                <button class="view-btn ${this.currentView === 'grid' ? 'active' : ''}" data-view="grid">
                    <span>⊞</span> Grid
                </button>
                <button class="view-btn ${this.currentView === 'list' ? 'active' : ''}" data-view="list">
                    <span>☰</span> List
                </button>
            </div>
        `;

        const toggleDiv = document.createElement('div');
        toggleDiv.innerHTML = toggleHTML;
        this.container.insertBefore(toggleDiv.firstElementChild, this.container.firstChild);

        // Add event listeners
        document.querySelectorAll('.view-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.switchView(e.target.closest('.view-btn').dataset.view);
            });
        });
    }

    switchView(view) {
        this.currentView = view;

        // Update active button
        document.querySelectorAll('.view-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });

        this.renderGallery();
    }

    renderGallery() {
        const galleryContainer = this.container.querySelector('.gallery-container') ||
            document.createElement('div');
        galleryContainer.className = `gallery-container ${this.currentView}-view`;
        galleryContainer.innerHTML = '';

        this.photos.forEach((photo, index) => {
            const photoCard = this.createPhotoCard(photo, index);
            galleryContainer.appendChild(photoCard);
        });

        if (!this.container.querySelector('.gallery-container')) {
            this.container.appendChild(galleryContainer);
        }
    }

    createPhotoCard(photo, index) {
        const card = document.createElement('div');
        card.className = 'photo-card';

        card.innerHTML = `
            <div class="photo-wrapper">
                <img src="${photo.src}" alt="${photo.title}" loading="lazy">
                <div class="photo-overlay">
                    <button class="fullscreen-btn" data-index="${index}">
                        <span>⛶</span> View Fullscreen
                    </button>
                </div>
            </div>
            ${this.currentView === 'list' ? `
                <div class="photo-info">
                    <h3>${photo.title}</h3>
                    <p>${photo.description || ''}</p>
                </div>
            ` : ''}
        `;

        // Add click handler for fullscreen
        card.querySelector('.fullscreen-btn').addEventListener('click', () => {
            this.openFullscreen(index);
        });

        // Also allow clicking the image itself
        card.querySelector('img').addEventListener('click', () => {
            this.openFullscreen(index);
        });

        return card;
    }

    openFullscreen(index) {
        this.currentPhotoIndex = index;
        const lightbox = this.createLightbox();
        document.body.appendChild(lightbox);
        document.body.style.overflow = 'hidden';
    }

    createLightbox() {
        const lightbox = document.createElement('div');
        lightbox.className = 'lightbox';
        lightbox.id = 'photo-lightbox';

        const photo = this.photos[this.currentPhotoIndex];

        lightbox.innerHTML = `
            <div class="lightbox-content">
                <button class="lightbox-close">&times;</button>
                <button class="lightbox-nav lightbox-prev" ${this.currentPhotoIndex === 0 ? 'disabled' : ''}>
                    ‹
                </button>
                <div class="lightbox-image-container">
                    <img src="${photo.src}" alt="${photo.title}">
                    <div class="lightbox-caption">
                        <h3>${photo.title}</h3>
                        <p class="photo-counter">${this.currentPhotoIndex + 1} / ${this.photos.length}</p>
                    </div>
                </div>
                <button class="lightbox-nav lightbox-next" ${this.currentPhotoIndex === this.photos.length - 1 ? 'disabled' : ''}>
                    ›
                </button>
            </div>
        `;

        // Event listeners
        lightbox.querySelector('.lightbox-close').addEventListener('click', () => this.closeLightbox());
        lightbox.querySelector('.lightbox-prev').addEventListener('click', () => this.navigateLightbox(-1));
        lightbox.querySelector('.lightbox-next').addEventListener('click', () => this.navigateLightbox(1));

        // Click outside to close
        lightbox.addEventListener('click', (e) => {
            if (e.target === lightbox) {
                this.closeLightbox();
            }
        });

        return lightbox;
    }

    navigateLightbox(direction) {
        const newIndex = this.currentPhotoIndex + direction;

        if (newIndex >= 0 && newIndex < this.photos.length) {
            this.currentPhotoIndex = newIndex;
            this.updateLightbox();
        }
    }

    updateLightbox() {
        const lightbox = document.getElementById('photo-lightbox');
        const photo = this.photos[this.currentPhotoIndex];

        lightbox.querySelector('img').src = photo.src;
        lightbox.querySelector('img').alt = photo.title;
        lightbox.querySelector('.lightbox-caption h3').textContent = photo.title;
        lightbox.querySelector('.photo-counter').textContent =
            `${this.currentPhotoIndex + 1} / ${this.photos.length}`;

        // Update button states
        lightbox.querySelector('.lightbox-prev').disabled = this.currentPhotoIndex === 0;
        lightbox.querySelector('.lightbox-next').disabled =
            this.currentPhotoIndex === this.photos.length - 1;
    }

    closeLightbox() {
        const lightbox = document.getElementById('photo-lightbox');
        if (lightbox) {
            lightbox.remove();
            document.body.style.overflow = '';
        }
    }

    setupKeyboardNavigation() {
        document.addEventListener('keydown', (e) => {
            const lightbox = document.getElementById('photo-lightbox');
            if (!lightbox) return;

            switch (e.key) {
                case 'Escape':
                    this.closeLightbox();
                    break;
                case 'ArrowLeft':
                    this.navigateLightbox(-1);
                    break;
                case 'ArrowRight':
                    this.navigateLightbox(1);
                    break;
            }
        });
    }
}

// Export for use in other files
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PhotoGallery;
}
