/**
 * Photo Gallery Component
 * - Grid/list/fullscreen modes
 * - OneDrive shared album/folder loader
 * - Local JSON manifest fallback
 */

class PhotoGallery {
    constructor(photos, containerId) {
        this.photos = Array.isArray(photos) ? photos : [];
        this.container = document.getElementById(containerId);
        this.currentView = 'grid';
        this.currentPhotoIndex = 0;
        this.viewToggle = null;
        this.galleryContainer = null;
        this.keyboardBound = false;
        this.keydownHandler = (event) => {
            if (PhotoGallery.activeGallery !== this) return;

            if (event.key === 'Escape') this.closeLightbox();
            if (event.key === 'ArrowLeft') this.navigateLightbox(-1);
            if (event.key === 'ArrowRight') this.navigateLightbox(1);
        };

        if (this.container && this.photos.length > 0) {
            this.init();
        }
    }

    static textOrDefault(value, fallback) {
        if (typeof value !== 'string') return fallback;
        const trimmed = value.trim();
        return trimmed.length > 0 ? trimmed : fallback;
    }

    static derivePhotoTitle(fileName, fallbackIndex) {
        if (typeof fileName !== 'string' || fileName.trim().length === 0) {
            return `Photo ${fallbackIndex}`;
        }

        return fileName
            .replace(/\.[^/.]+$/, '')
            .replace(/[-_]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    static derivePhotoDescription(title, fallbackDescription) {
        const normalizedTitle = PhotoGallery.textOrDefault(title, '');
        const normalizedFallback = PhotoGallery.textOrDefault(
            fallbackDescription,
            'Travel memory.'
        );

        if (!normalizedTitle) {
            return normalizedFallback;
        }

        const detail = normalizedTitle.includes(' - ')
            ? normalizedTitle.split(' - ').slice(1).join(' - ').trim()
            : normalizedTitle;

        if (/\d{1,2}\s[A-Za-z]{3}\s\d{4}/.test(detail)) {
            return `Captured on ${detail}.`;
        }

        return normalizedFallback;
    }

    static isConfiguredShareLink(shareLink) {
        return (
            typeof shareLink === 'string'
            && shareLink.trim().length > 0
            && !shareLink.includes('PASTE_')
        );
    }

    static isConfiguredApiEndpoint(apiEndpoint) {
        return (
            typeof apiEndpoint === 'string'
            && apiEndpoint.trim().length > 0
            && !apiEndpoint.includes('PASTE_')
        );
    }

    static async fetchJson(url) {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`OneDrive API request failed (${response.status}).`);
        }
        return response.json();
    }

    static extractItemsPage(data) {
        if (!data || typeof data !== 'object') {
            return { items: [], nextLink: null };
        }

        if (Array.isArray(data.value)) {
            return {
                items: data.value,
                nextLink: data['@odata.nextLink'] || null
            };
        }

        if (Array.isArray(data.children)) {
            return {
                items: data.children,
                nextLink: data['children@odata.nextLink'] || null
            };
        }

        return { items: [], nextLink: null };
    }

    static async collectPagedItems(firstPage) {
        const allItems = [];
        let currentPage = firstPage;
        let pageCount = 0;

        while (currentPage) {
            const { items, nextLink } = PhotoGallery.extractItemsPage(currentPage);
            allItems.push(...items);

            if (!nextLink) break;

            pageCount += 1;
            if (pageCount > 100) {
                throw new Error('OneDrive pagination did not terminate.');
            }

            currentPage = await PhotoGallery.fetchJson(nextLink);
        }

        return allItems;
    }

    static toOneDriveShareId(shareLink) {
        const encoder = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;
        const bytes = encoder
            ? encoder.encode(shareLink)
            : unescape(encodeURIComponent(shareLink)).split('').map((char) => char.charCodeAt(0));

        let binary = '';
        for (const byte of bytes) {
            binary += String.fromCharCode(byte);
        }

        return btoa(binary)
            .replace(/\//g, '_')
            .replace(/\+/g, '-')
            .replace(/=+$/g, '');
    }

    static async fetchFirstWorkingCollection(endpoints) {
        let lastError = null;

        for (const endpoint of endpoints) {
            try {
                const firstPage = await PhotoGallery.fetchJson(endpoint);
                const items = await PhotoGallery.collectPagedItems(firstPage);
                if (items.length > 0) {
                    return items;
                }
            } catch (error) {
                lastError = error;
            }
        }

        throw lastError || new Error('Could not retrieve data from OneDrive.');
    }

    static isImageItem(item) {
        if (!item || typeof item !== 'object') return false;
        if (item.image) return true;

        const mimeType = item.file && typeof item.file.mimeType === 'string'
            ? item.file.mimeType.toLowerCase()
            : '';

        return mimeType.startsWith('image/');
    }

    static itemDownloadUrl(item) {
        if (!item || typeof item !== 'object') return '';

        return (
            item['@content.downloadUrl']
            || item['@microsoft.graph.downloadUrl']
            || item.webUrl
            || ''
        );
    }

    static mapOneDriveItemsToPhotos(items, description) {
        const photos = [];
        const seen = new Set();
        const sorted = [...items].sort((left, right) => {
            const leftName = PhotoGallery.textOrDefault(left && left.name, '');
            const rightName = PhotoGallery.textOrDefault(right && right.name, '');
            return leftName.localeCompare(rightName, undefined, {
                numeric: true,
                sensitivity: 'base'
            });
        });

        for (const item of sorted) {
            if (!PhotoGallery.isImageItem(item)) continue;

            const src = PhotoGallery.itemDownloadUrl(item);
            if (!src) continue;

            const uniqueKey = item.id || src;
            if (seen.has(uniqueKey)) continue;
            seen.add(uniqueKey);

            const title = PhotoGallery.derivePhotoTitle(item.name, photos.length + 1);
            photos.push({
                src,
                title,
                description: PhotoGallery.derivePhotoDescription(title, description)
            });
        }

        return photos;
    }

    static async fetchOneDriveItems(shareLink) {
        const encodedShareId = PhotoGallery.toOneDriveShareId(shareLink);
        const shareId = `u!${encodedShareId}`;
        const baseUrl = `https://api.onedrive.com/v1.0/shares/${shareId}`;

        const endpoints = [
            `${baseUrl}/driveItem/children?$top=200`,
            `${baseUrl}/root/children?$top=200`,
            `${baseUrl}/driveItem?$expand=children`
        ];

        return PhotoGallery.fetchFirstWorkingCollection(endpoints);
    }

    static renderLoading(container, message) {
        if (!container) return;
        container.innerHTML = `<p class="loading" style="text-align: center; padding: 2rem;">${message}</p>`;
    }

    static renderComingSoon(container, destinationName) {
        if (!container) return;

        const safeDestination = PhotoGallery.escapeHtml(
            PhotoGallery.textOrDefault(destinationName, 'this destination')
        );
        container.innerHTML = `
            <div class="gallery-coming-soon">
                <h3>Coming Soon</h3>
                <p>Photos for ${safeDestination} will be available soon.</p>
            </div>
        `;
    }

    static renderNoPhotos(container, destinationName) {
        if (!container) return;

        const safeDestination = PhotoGallery.escapeHtml(
            PhotoGallery.textOrDefault(destinationName, 'this destination')
        );
        container.innerHTML = `
            <div class="gallery-coming-soon">
                <h3>No Photos to Show</h3>
                <p>No photos are available yet for ${safeDestination}.</p>
            </div>
        `;
    }

    static classifyLoadState(message, statusCode = 0) {
        const normalized = PhotoGallery.textOrDefault(message, '').toLowerCase();

        if (
            normalized.includes('no image files')
            || normalized.includes('no image entries')
            || normalized.includes('does not contain images')
            || normalized.includes('no photos')
            || normalized.includes('empty manifest')
        ) {
            return 'empty';
        }

        if (
            statusCode === 404
            || normalized.includes('not found')
            || normalized.includes('manifest not found')
            || normalized.includes('missing')
        ) {
            return 'missing';
        }

        return 'error';
    }

    static renderStatusFallback(container, destinationName, state) {
        if (state === 'empty') {
            PhotoGallery.renderNoPhotos(container, destinationName);
            return;
        }

        PhotoGallery.renderComingSoon(container, destinationName);
    }

    static escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    static normalizeExternalUrl(rawUrl) {
        const trimmed = PhotoGallery.textOrDefault(rawUrl, '');
        if (!trimmed) return '';

        try {
            const baseUrl = (
                typeof window !== 'undefined'
                && window.location
                && typeof window.location.href === 'string'
            ) ? window.location.href : 'https://localhost/';

            const parsed = new URL(trimmed, baseUrl);
            if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                return parsed.toString();
            }
        } catch (error) {
            return '';
        }

        return '';
    }

    static renderError(container, destinationName, message) {
        if (!container) return;

        const safeDestination = PhotoGallery.textOrDefault(destinationName, 'this trip');
        const safeMessage = PhotoGallery.textOrDefault(
            message,
            'Could not connect to OneDrive.'
        );
        const escapedDestination = PhotoGallery.escapeHtml(safeDestination);
        const escapedMessage = PhotoGallery.escapeHtml(safeMessage);

        container.innerHTML = `
            <div class="gallery-error" style="padding: 3rem; text-align: center; background: var(--bg-secondary); border-radius: 24px; border: 1px solid var(--color-coral); margin: 2rem 0;">
                <h3 style="color: var(--color-coral); margin-bottom: 1rem;">Gallery Not Available</h3>
                <p style="margin-bottom: 1rem; color: var(--text-primary);">Unable to load photos for ${escapedDestination}.</p>
                <p style="margin-bottom: 1rem; color: var(--text-primary);">${escapedMessage}</p>
                <ol style="padding-left: 1.2rem; line-height: 1.8; text-align: left; display: inline-block;">
                    <li>Use a OneDrive shared album/folder link with "Anyone with the link can view".</li>
                    <li>If you use local fallback, ensure the manifest JSON exists in <code>data/</code>.</li>
                </ol>
            </div>
        `;
    }

    static renderOneDriveOpenFallback(container, shareLink, destinationName, message) {
        if (!container) return;

        const href = PhotoGallery.normalizeExternalUrl(shareLink);
        if (!href) {
            PhotoGallery.renderError(container, destinationName, message);
            return;
        }

        const safeDestination = PhotoGallery.escapeHtml(
            PhotoGallery.textOrDefault(destinationName, 'this trip')
        );
        const rawMessage = PhotoGallery.textOrDefault(message, 'Could not connect to OneDrive.');
        const safeMessage = PhotoGallery.escapeHtml(rawMessage);
        const safeHref = PhotoGallery.escapeHtml(href);
        const isFilePreview = (
            typeof window !== 'undefined'
            && window.location
            && window.location.protocol === 'file:'
        );
        const likelyApiBlock = /401|403|unauthorized|forbidden|failed to fetch/i.test(rawMessage);
        const primaryHint = isFilePreview
            ? 'You are previewing from file://. Browser CORS rules block OneDrive API calls in this mode.'
            : (likelyApiBlock
                ? 'OneDrive is refusing anonymous API listing for this shared album.'
                : 'The OneDrive API request did not complete.');
        const escapedPrimaryHint = PhotoGallery.escapeHtml(primaryHint);

        container.innerHTML = `
            <div class="gallery-error" style="padding: 3rem; text-align: center; background: var(--bg-secondary); border-radius: 24px; border: 1px solid var(--color-coral); margin: 2rem 0;">
                <h3 style="color: var(--color-coral); margin-bottom: 1rem;">Open OneDrive Album</h3>
                <p style="margin-bottom: 1rem; color: var(--text-primary);">Unable to list files for ${safeDestination} via API.</p>
                <p style="margin-bottom: 1rem; color: var(--text-primary);">${escapedPrimaryHint}</p>
                <p style="margin-bottom: 1rem; color: var(--text-primary);">${safeMessage}</p>
                <p style="margin-bottom: 1.5rem; color: var(--text-primary);">
                    Open the shared album directly:
                </p>
                <p style="margin-bottom: 1.2rem;">
                    <a href="${safeHref}" target="_blank" rel="noopener noreferrer" class="btn btn-primary">Open Shared Album</a>
                </p>
                <ol style="padding-left: 1.2rem; line-height: 1.8; text-align: left; display: inline-block; margin-top: 0.75rem;">
                    <li>If testing locally, run a server instead of opening HTML directly: <code>python -m http.server 5500</code>.</li>
                    <li>For a grid gallery on your site, use local cache JSON + images under <code>data/</code> and <code>photos/</code>.</li>
                    <li>If you need live OneDrive API listing, add a backend token proxy (Graph delegated auth).</li>
                </ol>
            </div>
        `;
    }

    static parseLocalManifest(data, description) {
        const rows = Array.isArray(data)
            ? data
            : (Array.isArray(data && data.photos) ? data.photos : []);

        return rows
            .filter((row) => row && typeof row === 'object')
            .map((row, index) => {
                const title = PhotoGallery.textOrDefault(
                    row.title,
                    PhotoGallery.derivePhotoTitle(row.name, index + 1)
                );
                return {
                    src: PhotoGallery.textOrDefault(row.src, ''),
                    title,
                    description: PhotoGallery.textOrDefault(
                        row.description,
                        PhotoGallery.derivePhotoDescription(title, description)
                    )
                };
            })
            .filter((photo) => photo.src.length > 0);
    }

    static parseApiManifest(data, description) {
        const rows = Array.isArray(data)
            ? data
            : (Array.isArray(data && data.photos) ? data.photos : []);

        return rows
            .filter((row) => row && typeof row === 'object')
            .map((row, index) => {
                const src = PhotoGallery.textOrDefault(
                    row.src,
                    PhotoGallery.textOrDefault(
                        row.thumbnail,
                        PhotoGallery.textOrDefault(row.downloadUrl, '')
                    )
                );

                const title = PhotoGallery.textOrDefault(
                    row.title,
                    PhotoGallery.derivePhotoTitle(row.name, index + 1)
                );
                return {
                    src,
                    title,
                    description: PhotoGallery.textOrDefault(
                        row.description,
                        PhotoGallery.derivePhotoDescription(title, description)
                    )
                };
            })
            .filter((photo) => photo.src.length > 0);
    }

    static async fromApi(apiEndpoint, containerId, options = {}) {
        const gallery = new PhotoGallery([], containerId);
        const container = document.getElementById(containerId);
        const {
            suppressErrorUi = false,
            description = 'Captured during the journey.',
            destinationName = 'this trip'
        } = options;
        let responseStatus = 0;

        try {
            PhotoGallery.lastApiState = 'success';
            PhotoGallery.lastApiErrorMessage = '';
            PhotoGallery.renderLoading(container, 'Loading photos from gallery API...');

            const response = await fetch(apiEndpoint, {
                headers: { Accept: 'application/json' }
            });
            responseStatus = response.status;
            if (!response.ok) {
                throw new Error(`Gallery API request failed (${response.status}).`);
            }

            const data = await response.json();
            gallery.photos = PhotoGallery.parseApiManifest(data, description);

            if (gallery.photos.length === 0) {
                throw new Error('Gallery API returned no image entries.');
            }

            if (container) container.innerHTML = '';
            PhotoGallery.lastApiState = 'success';
            gallery.init();
            return gallery;
        } catch (error) {
            console.error('Gallery API load failed:', error);
            const message = PhotoGallery.textOrDefault(
                error && error.message,
                'Could not connect to gallery API.'
            );
            PhotoGallery.lastApiErrorMessage = message;
            PhotoGallery.lastApiState = PhotoGallery.classifyLoadState(message, responseStatus);

            if (container && !suppressErrorUi) {
                PhotoGallery.renderStatusFallback(container, destinationName, PhotoGallery.lastApiState);
            }
            return null;
        }
    }

    static async fromLocal(jsonPath, containerId, options = {}) {
        const gallery = new PhotoGallery([], containerId);
        const container = document.getElementById(containerId);
        const {
            suppressErrorUi = false,
            description = 'Memories from this trip.',
            destinationName = 'this trip'
        } = options;
        let responseStatus = 0;

        try {
            PhotoGallery.lastLocalState = 'success';
            const response = await fetch(jsonPath);
            responseStatus = response.status;
            if (!response.ok) {
                throw new Error('Local manifest not found.');
            }

            const data = await response.json();
            gallery.photos = PhotoGallery.parseLocalManifest(data, description);

            if (gallery.photos.length === 0) {
                throw new Error('Local manifest does not contain images.');
            }

            PhotoGallery.lastLocalState = 'success';
            gallery.init();
            return gallery;
        } catch (error) {
            console.error('Local gallery load failed:', error);
            const message = PhotoGallery.textOrDefault(
                error && error.message,
                'Could not load local gallery.'
            );
            PhotoGallery.lastLocalState = PhotoGallery.classifyLoadState(message, responseStatus);
            if (container && !suppressErrorUi) {
                PhotoGallery.renderStatusFallback(container, destinationName, PhotoGallery.lastLocalState);
            }
            return null;
        }
    }

    static async fromOneDrive(shareLink, containerId, options = {}) {
        const gallery = new PhotoGallery([], containerId);
        const container = document.getElementById(containerId);
        const {
            suppressErrorUi = false,
            description = 'Captured during the journey.',
            destinationName = 'this trip'
        } = options;

        try {
            PhotoGallery.lastOneDriveState = 'success';
            PhotoGallery.lastOneDriveErrorMessage = '';
            PhotoGallery.renderLoading(container, 'Loading photos from OneDrive...');

            const items = await PhotoGallery.fetchOneDriveItems(shareLink);
            gallery.photos = PhotoGallery.mapOneDriveItemsToPhotos(items, description);

            if (gallery.photos.length === 0) {
                throw new Error('No image files were found in this shared album.');
            }

            if (container) container.innerHTML = '';
            PhotoGallery.lastOneDriveState = 'success';
            gallery.init();
            return gallery;
        } catch (error) {
            console.error('OneDrive gallery load failed:', error);
            const message = PhotoGallery.textOrDefault(
                error && error.message,
                'Could not connect to OneDrive.'
            );
            PhotoGallery.lastOneDriveErrorMessage = message;
            PhotoGallery.lastOneDriveState = PhotoGallery.classifyLoadState(message, 0);

            if (container && !suppressErrorUi) {
                PhotoGallery.renderStatusFallback(container, destinationName, PhotoGallery.lastOneDriveState);
            }
            return null;
        }
    }

    static async loadBestSource({
        containerId,
        apiEndpoint,
        shareLink,
        localJsonPath,
        destinationName = 'journey'
    }) {
        const description = `Memories from ${destinationName}.`;
        const hasApiEndpoint = PhotoGallery.isConfiguredApiEndpoint(apiEndpoint);
        const hasShareLink = PhotoGallery.isConfiguredShareLink(shareLink);
        let apiErrorMessage = '';
        let oneDriveErrorMessage = '';
        const statusSignals = [];

        if (hasApiEndpoint) {
            const apiGallery = await PhotoGallery.fromApi(apiEndpoint.trim(), containerId, {
                suppressErrorUi: true,
                description,
                destinationName
            });

            if (apiGallery) {
                return apiGallery;
            }

            statusSignals.push(PhotoGallery.textOrDefault(PhotoGallery.lastApiState, 'error'));
            apiErrorMessage = PhotoGallery.textOrDefault(
                PhotoGallery.lastApiErrorMessage,
                ''
            );
        }

        if (hasShareLink) {
            const remoteGallery = await PhotoGallery.fromOneDrive(shareLink.trim(), containerId, {
                suppressErrorUi: true,
                description,
                destinationName
            });

            if (remoteGallery) {
                return remoteGallery;
            }

            statusSignals.push(PhotoGallery.textOrDefault(PhotoGallery.lastOneDriveState, 'error'));
            oneDriveErrorMessage = PhotoGallery.textOrDefault(
                PhotoGallery.lastOneDriveErrorMessage,
                ''
            );
        }

        if (typeof localJsonPath === 'string' && localJsonPath.trim().length > 0) {
            const localGallery = await PhotoGallery.fromLocal(localJsonPath, containerId, {
                suppressErrorUi: true,
                description,
                destinationName
            });

            if (localGallery) {
                return localGallery;
            }

            statusSignals.push(PhotoGallery.textOrDefault(PhotoGallery.lastLocalState, 'error'));
        }

        const container = document.getElementById(containerId);
        if (container) {
            const mergedFailureMessage = [apiErrorMessage, oneDriveErrorMessage]
                .filter((entry) => typeof entry === 'string' && entry.trim().length > 0)
                .join(' | ');
            if (mergedFailureMessage) {
                console.warn(`Gallery load fallback for ${destinationName}: ${mergedFailureMessage}`);
            }

            if (statusSignals.includes('empty')) {
                PhotoGallery.renderNoPhotos(container, destinationName);
            } else {
                PhotoGallery.renderComingSoon(container, destinationName);
            }
        }

        return null;
    }

    static resolveSourceMode(rawMode, fallback = 'auto') {
        const normalized = PhotoGallery.textOrDefault(rawMode, '').toLowerCase();

        if (normalized === 'auto' || normalized === 'gallery' || normalized === 'best') {
            return 'auto';
        }

        if (
            normalized === 'album'
            || normalized === 'direct'
            || normalized === 'shared-album'
            || normalized === 'onedrive'
        ) {
            return 'album';
        }

        return fallback;
    }

    static safeLocalStorageGet(key) {
        if (typeof window === 'undefined' || !window.localStorage) return '';
        try {
            return PhotoGallery.textOrDefault(window.localStorage.getItem(key), '');
        } catch (error) {
            return '';
        }
    }

    static safeLocalStorageSet(key, value) {
        if (typeof window === 'undefined' || !window.localStorage) return;
        try {
            window.localStorage.setItem(key, value);
        } catch (error) {
            // Ignore storage failures (private mode or blocked storage).
        }
    }

    static getInitialSourceMode(storageKey, defaultMode = 'auto') {
        let queryMode = '';

        if (
            typeof window !== 'undefined'
            && window.location
            && typeof window.location.search === 'string'
        ) {
            try {
                const params = new URLSearchParams(window.location.search);
                queryMode = PhotoGallery.textOrDefault(
                    params.get('galleryMode')
                    || params.get('sourceMode')
                    || params.get('gallery'),
                    ''
                );
            } catch (error) {
                queryMode = '';
            }
        }

        if (queryMode) {
            const resolvedFromQuery = PhotoGallery.resolveSourceMode(queryMode, '');
            if (resolvedFromQuery) {
                PhotoGallery.safeLocalStorageSet(storageKey, resolvedFromQuery);
                return resolvedFromQuery;
            }
        }

        const storedMode = PhotoGallery.safeLocalStorageGet(storageKey);
        return PhotoGallery.resolveSourceMode(storedMode, defaultMode);
    }

    static renderDirectAlbumMode(container, shareLink, destinationName) {
        if (!container) return;

        const href = PhotoGallery.normalizeExternalUrl(shareLink);
        if (!href) {
            PhotoGallery.renderError(
                container,
                destinationName,
                'Direct album mode is enabled, but shared link is missing or invalid.'
            );
            return;
        }

        const safeDestination = PhotoGallery.escapeHtml(
            PhotoGallery.textOrDefault(destinationName, 'this trip')
        );
        const safeHref = PhotoGallery.escapeHtml(href);

        container.innerHTML = `
            <div class="gallery-error" style="padding: 3rem; text-align: center; background: var(--bg-secondary); border-radius: 24px; border: 1px solid var(--color-coral); margin: 1rem 0 2rem;">
                <h3 style="color: var(--color-coral); margin-bottom: 1rem;">Direct Album Mode</h3>
                <p style="margin-bottom: 1rem; color: var(--text-primary);">
                    Live shared album mode is enabled for ${safeDestination}.
                </p>
                <p style="margin-bottom: 1.4rem; color: var(--text-primary);">
                    Open the OneDrive shared album directly:
                </p>
                <p style="margin-bottom: 1.1rem;">
                    <a href="${safeHref}" target="_blank" rel="noopener noreferrer" class="btn btn-primary">Open Shared Album</a>
                </p>
                <p style="color: var(--text-secondary); font-size: 0.95rem;">
                    Switch back to <strong>Auto Gallery</strong> above after folder-based sync is ready.
                </p>
            </div>
        `;
    }

    static async loadWithSourceToggle({
        containerId,
        apiEndpoint,
        shareLink,
        localJsonPath,
        destinationName = 'journey',
        defaultMode = 'auto',
        modeStorageKey = '',
        showModeToggle = false
    }) {
        const hostContainer = document.getElementById(containerId);
        if (!hostContainer) return null;

        const normalizedDefaultMode = PhotoGallery.resolveSourceMode(defaultMode, 'auto');
        const storageKey = PhotoGallery.textOrDefault(
            modeStorageKey,
            `photo-gallery-mode:${containerId}`
        );
        const initialMode = showModeToggle
            ? PhotoGallery.getInitialSourceMode(storageKey, normalizedDefaultMode)
            : normalizedDefaultMode;
        const contentId = `${containerId}__content`;

        if (!showModeToggle) {
            PhotoGallery.safeLocalStorageSet(storageKey, normalizedDefaultMode);
        }

        hostContainer.innerHTML = '';

        let autoButton = null;
        let albumButton = null;
        let modeHint = null;

        if (showModeToggle) {
            const toggle = document.createElement('div');
            toggle.className = 'view-toggle';
            toggle.style.justifyContent = 'center';
            toggle.style.marginBottom = '0.6rem';

            autoButton = document.createElement('button');
            autoButton.type = 'button';
            autoButton.className = 'view-btn';
            autoButton.textContent = 'Auto Gallery';

            albumButton = document.createElement('button');
            albumButton.type = 'button';
            albumButton.className = 'view-btn';
            albumButton.textContent = 'Direct Album';

            toggle.appendChild(autoButton);
            toggle.appendChild(albumButton);

            modeHint = document.createElement('p');
            modeHint.style.textAlign = 'center';
            modeHint.style.marginBottom = '1rem';
            modeHint.style.color = 'var(--text-secondary)';
            modeHint.style.fontSize = '0.95rem';

            hostContainer.appendChild(toggle);
            hostContainer.appendChild(modeHint);
        }

        const content = document.createElement('div');
        content.id = contentId;

        hostContainer.appendChild(content);

        const setModeUi = (mode) => {
            if (autoButton) {
                autoButton.classList.toggle('active', mode === 'auto');
            }
            if (albumButton) {
                albumButton.classList.toggle('active', mode === 'album');
            }
            if (modeHint) {
                modeHint.textContent = mode === 'album'
                    ? 'Direct Album: opens OneDrive shared album directly.'
                    : 'Auto Gallery: API -> OneDrive -> local cache fallback.';
            }
        };

        const applyMode = async (mode, persist) => {
            const resolvedMode = PhotoGallery.resolveSourceMode(mode, normalizedDefaultMode);
            if (persist) {
                PhotoGallery.safeLocalStorageSet(storageKey, resolvedMode);
            }

            setModeUi(resolvedMode);

            if (resolvedMode === 'album') {
                PhotoGallery.renderDirectAlbumMode(content, shareLink, destinationName);
                return null;
            }

            return PhotoGallery.loadBestSource({
                containerId: contentId,
                apiEndpoint,
                shareLink,
                localJsonPath,
                destinationName
            });
        };

        if (autoButton) {
            autoButton.addEventListener('click', () => {
                void applyMode('auto', true);
            });
        }

        if (albumButton) {
            albumButton.addEventListener('click', () => {
                void applyMode('album', true);
            });
        }

        return applyMode(initialMode, false);
    }

    init() {
        if (!this.container) return;

        this.container.innerHTML = '';
        this.renderViewToggle();
        this.renderGallery();
        this.setupKeyboardNavigation();
    }

    renderViewToggle() {
        const toggle = document.createElement('div');
        toggle.className = 'view-toggle';

        const views = [
            { key: 'grid', label: 'Grid' },
            { key: 'list', label: 'List' }
        ];

        for (const view of views) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `view-btn ${this.currentView === view.key ? 'active' : ''}`;
            button.dataset.view = view.key;
            button.textContent = view.label;
            button.addEventListener('click', () => this.switchView(view.key));
            toggle.appendChild(button);
        }

        this.viewToggle = toggle;
        this.container.appendChild(toggle);
    }

    switchView(view) {
        if (view !== 'grid' && view !== 'list') return;
        if (this.currentView === view) return;

        this.currentView = view;
        this.refreshToggleState();
        this.renderGallery();
    }

    refreshToggleState() {
        if (!this.viewToggle) return;

        this.viewToggle.querySelectorAll('.view-btn').forEach((button) => {
            button.classList.toggle('active', button.dataset.view === this.currentView);
        });
    }

    renderGallery() {
        if (!this.container) return;

        if (!this.galleryContainer) {
            this.galleryContainer = document.createElement('div');
            this.container.appendChild(this.galleryContainer);
        }

        this.galleryContainer.className = `gallery-container ${this.currentView}-view`;
        this.galleryContainer.innerHTML = '';

        this.photos.forEach((photo, index) => {
            this.galleryContainer.appendChild(this.createPhotoCard(photo, index));
        });
    }

    createPhotoCard(photo, index) {
        const card = document.createElement('div');
        card.className = 'photo-card';
        card.tabIndex = 0;
        card.setAttribute('role', 'button');
        card.setAttribute('aria-label', `Open ${photo.title} in fullscreen`);

        const wrapper = document.createElement('div');
        wrapper.className = 'photo-wrapper';

        const image = document.createElement('img');
        image.src = photo.src;
        image.alt = photo.title;
        image.loading = 'lazy';
        image.addEventListener('click', () => this.openFullscreen(index));

        const overlay = document.createElement('div');
        overlay.className = 'photo-overlay';

        const fullButton = document.createElement('button');
        fullButton.type = 'button';
        fullButton.className = 'fullscreen-btn';
        fullButton.dataset.index = `${index}`;
        fullButton.textContent = 'View Fullscreen';
        fullButton.addEventListener('click', () => this.openFullscreen(index));

        overlay.appendChild(fullButton);
        wrapper.appendChild(image);
        wrapper.appendChild(overlay);
        card.appendChild(wrapper);

        if (this.currentView === 'list') {
            const info = document.createElement('div');
            info.className = 'photo-info';

            const title = document.createElement('h3');
            title.textContent = photo.title;
            info.appendChild(title);

            const description = document.createElement('p');
            description.textContent = PhotoGallery.textOrDefault(photo.description, '');
            info.appendChild(description);

            card.appendChild(info);
        }

        card.addEventListener('click', (event) => {
            const target = event.target;
            if (target instanceof Element && target.closest('.fullscreen-btn')) {
                return;
            }
            this.openFullscreen(index);
        });

        card.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            this.openFullscreen(index);
        });

        return card;
    }

    openFullscreen(index) {
        if (index < 0 || index >= this.photos.length) return;

        this.currentPhotoIndex = index;
        PhotoGallery.activeGallery = this;

        const previousLightbox = document.getElementById('photo-lightbox');
        if (previousLightbox) {
            previousLightbox.remove();
        }

        const lightbox = this.createLightbox();
        document.body.appendChild(lightbox);
        this.updateLightbox(lightbox);
        document.body.style.overflow = 'hidden';
    }

    createLightbox() {
        const lightbox = document.createElement('div');
        lightbox.className = 'lightbox';
        lightbox.id = 'photo-lightbox';

        const content = document.createElement('div');
        content.className = 'lightbox-content';

        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'lightbox-close';
        closeButton.setAttribute('aria-label', 'Close');
        closeButton.innerHTML = '&times;';
        closeButton.addEventListener('click', () => this.closeLightbox());

        const prevButton = document.createElement('button');
        prevButton.type = 'button';
        prevButton.className = 'lightbox-nav lightbox-prev';
        prevButton.textContent = '<';
        prevButton.addEventListener('click', () => this.navigateLightbox(-1));

        const imageContainer = document.createElement('div');
        imageContainer.className = 'lightbox-image-container';

        const image = document.createElement('img');
        imageContainer.appendChild(image);

        const caption = document.createElement('div');
        caption.className = 'lightbox-caption';

        const title = document.createElement('h3');
        title.className = 'lightbox-title';

        const counter = document.createElement('p');
        counter.className = 'photo-counter';

        caption.appendChild(title);
        caption.appendChild(counter);
        imageContainer.appendChild(caption);

        const nextButton = document.createElement('button');
        nextButton.type = 'button';
        nextButton.className = 'lightbox-nav lightbox-next';
        nextButton.textContent = '>';
        nextButton.addEventListener('click', () => this.navigateLightbox(1));

        content.appendChild(closeButton);
        content.appendChild(prevButton);
        content.appendChild(imageContainer);
        content.appendChild(nextButton);
        lightbox.appendChild(content);

        lightbox.addEventListener('click', (event) => {
            if (event.target === lightbox) {
                this.closeLightbox();
            }
        });

        return lightbox;
    }

    navigateLightbox(direction) {
        const nextIndex = this.currentPhotoIndex + direction;
        if (nextIndex < 0 || nextIndex >= this.photos.length) return;

        this.currentPhotoIndex = nextIndex;
        this.updateLightbox();
    }

    updateLightbox(lightboxElement = null) {
        const lightbox = lightboxElement || document.getElementById('photo-lightbox');
        if (!lightbox) return;

        const photo = this.photos[this.currentPhotoIndex];
        if (!photo) return;

        const image = lightbox.querySelector('.lightbox-image-container img');
        const title = lightbox.querySelector('.lightbox-caption .lightbox-title');
        const counter = lightbox.querySelector('.lightbox-caption .photo-counter');
        const prevButton = lightbox.querySelector('.lightbox-prev');
        const nextButton = lightbox.querySelector('.lightbox-next');

        image.src = photo.src;
        image.alt = photo.title;
        title.textContent = photo.title;
        counter.textContent = `${this.currentPhotoIndex + 1} / ${this.photos.length}`;

        prevButton.disabled = this.currentPhotoIndex === 0;
        nextButton.disabled = this.currentPhotoIndex === this.photos.length - 1;
    }

    closeLightbox() {
        const lightbox = document.getElementById('photo-lightbox');
        if (lightbox) {
            lightbox.remove();
        }

        document.body.style.overflow = '';
        if (PhotoGallery.activeGallery === this) {
            PhotoGallery.activeGallery = null;
        }
    }

    setupKeyboardNavigation() {
        if (this.keyboardBound) return;

        document.addEventListener('keydown', this.keydownHandler);
        this.keyboardBound = true;
    }
}

PhotoGallery.activeGallery = null;
PhotoGallery.lastApiErrorMessage = '';
PhotoGallery.lastOneDriveErrorMessage = '';

if (typeof module !== 'undefined' && module.exports) {
    module.exports = PhotoGallery;
}
