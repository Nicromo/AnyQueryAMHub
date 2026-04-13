/**
 * AM Hub Dashboard - Complete Account Management System
 * Real-time interactive dashboard with client management, tasks, meetings, and analytics
 */

class Dashboard {
    constructor() {
        this.user = null;
        this.clients = [];
        this.tasks = [];
        this.meetings = [];
        this.stats = {};
        this.currentView = 'dashboard';
        this.currentClientId = null;
        this.token = localStorage.getItem('token');
        this.ws = null;
        this.init();
    }

    // ======================== INITIALIZATION ========================

    async init() {
        console.log('🚀 Initializing Dashboard...');
        
        // Check authentication
        if (!this.token) {
            this.showLoginForm();
            return;
        }

        try {
            // Load user profile
            await this.loadProfile();
            // Load data
            await Promise.all([
                this.loadClients(),
                this.loadStats(),
            ]);
            // Initialize WebSocket
            this.initWebSocket();
            // Render dashboard
            this.renderDashboard();
            // Setup event listeners
            this.setupEventListeners();
        } catch (err) {
            console.error('Init error:', err);
            if (err.status === 401) {
                this.showLoginForm();
            } else {
                this.showError('Failed to load dashboard');
            }
        }
    }

    // ======================== AUTHENTICATION ========================

    async login(email, password) {
        try {
            const response = await this.apiCall('/api/auth/login', 'POST', {
                email,
                password,
            }, false);

            this.token = response.access_token;
            this.user = response.user;
            localStorage.setItem('token', this.token);
            localStorage.setItem('user', JSON.stringify(this.user));
            
            await this.init();
        } catch (err) {
            this.showError('Login failed: ' + err.message);
        }
    }

    async register(email, password, name) {
        try {
            await this.apiCall('/api/auth/register', 'POST', {
                email,
                password,
                name,
            }, false);

            this.showSuccess('Registration successful! Please login.');
            this.showLoginForm();
        } catch (err) {
            this.showError('Registration failed: ' + err.message);
        }
    }

    logout() {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        this.token = null;
        this.user = null;
        window.location.reload();
    }

    showLoginForm() {
        const app = document.getElementById('app');
        app.innerHTML = `
            <div class="auth-container">
                <div class="auth-card">
                    <h1>🎛️ AM Hub</h1>
                    <p>Account Manager Hub</p>
                    
                    <div id="auth-form">
                        <div class="form-group">
                            <label>Email</label>
                            <input type="email" id="auth-email" placeholder="your@email.com" />
                        </div>
                        <div class="form-group">
                            <label>Password</label>
                            <input type="password" id="auth-password" placeholder="••••••••" />
                        </div>
                        <div class="form-group" id="name-group" style="display: none;">
                            <label>Name</label>
                            <input type="text" id="auth-name" placeholder="Your Name" />
                        </div>
                        <button onclick="dashboard.handleAuthSubmit()" class="btn btn-primary btn-block">
                            <span id="auth-btn-text">Login</span>
                        </button>
                        <p style="text-align: center; margin-top: 16px;">
                            <a href="#" onclick="dashboard.toggleAuthMode(event)">
                                Don't have account? Register
                            </a>
                        </p>
                    </div>
                </div>
            </div>
        `;

        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            .auth-container {
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }
            .auth-card {
                background: white;
                padding: 2rem;
                border-radius: 12px;
                width: 100%;
                max-width: 400px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            }
            .auth-card h1 {
                margin: 0 0 4px;
                text-align: center;
                color: #333;
            }
            .auth-card p {
                text-align: center;
                color: #999;
                margin-bottom: 2rem;
            }
            .form-group {
                margin-bottom: 1.5rem;
            }
            .form-group label {
                display: block;
                margin-bottom: 0.5rem;
                font-weight: 500;
                color: #333;
            }
            .form-group input {
                width: 100%;
                padding: 0.75rem;
                border: 1px solid #ddd;
                border-radius: 6px;
                font-size: 0.95rem;
            }
            .form-group input:focus {
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102,126,234,0.1);
            }
            .btn-block {
                width: 100%;
            }
        `;
        document.head.appendChild(style);

        this.authMode = 'login';
    }

    toggleAuthMode(e) {
        e.preventDefault();
        const nameGroup = document.getElementById('name-group');
        const btnText = document.getElementById('auth-btn-text');
        
        if (this.authMode === 'login') {
            this.authMode = 'register';
            nameGroup.style.display = 'block';
            btnText.textContent = 'Register';
            e.target.textContent = 'Already have account? Login';
        } else {
            this.authMode = 'login';
            nameGroup.style.display = 'none';
            btnText.textContent = 'Login';
            e.target.textContent = "Don't have account? Register";
        }
    }

    handleAuthSubmit() {
        const email = document.getElementById('auth-email').value;
        const password = document.getElementById('auth-password').value;

        if (!email || !password) {
            this.showError('Please fill all fields');
            return;
        }

        if (this.authMode === 'login') {
            this.login(email, password);
        } else {
            const name = document.getElementById('auth-name').value;
            if (!name) {
                this.showError('Please enter your name');
                return;
            }
            this.register(email, password, name);
        }
    }

    // ======================== DATA LOADING ========================

    async loadProfile() {
        try {
            this.user = await this.apiCall('/api/me', 'GET');
            localStorage.setItem('user', JSON.stringify(this.user));
        } catch (err) {
            console.error('Failed to load profile:', err);
            throw err;
        }
    }

    async loadClients(skip = 0, limit = 50) {
        try {
            const response = await this.apiCall(
                `/api/clients?skip=${skip}&limit=${limit}`,
                'GET'
            );
            this.clients = response.data || response;
            return this.clients;
        } catch (err) {
            console.error('Failed to load clients:', err);
            this.showError('Failed to load clients');
        }
    }

    async load360Dashboard() {
        try {
            // Load enriched client data with task and meeting counts
            const response = await this.apiCall(
                `/api/dashboard/clients-summary`,
                'GET'
            );
            this.clients = response.data || response;
            return this.clients;
        } catch (err) {
            console.error('Failed to load 360 dashboard:', err);
            // Fallback to regular clients load
            return this.loadClients();
        }
    }

    async loadClientDetails(clientId) {
        try {
            const client = await this.apiCall(`/api/clients/${clientId}`, 'GET');
            const tasks = await this.apiCall(`/api/clients/${clientId}/tasks?limit=100`, 'GET');
            const meetings = await this.apiCall(`/api/clients/${clientId}/meetings?limit=100`, 'GET');
            
            return {
                client,
                tasks: tasks.data || tasks,
                meetings: meetings.data || meetings,
            };
        } catch (err) {
            console.error('Failed to load client details:', err);
            throw err;
        }
    }

    async loadStats() {
        try {
            this.stats = await this.apiCall('/api/stats', 'GET');
        } catch (err) {
            console.error('Failed to load stats:', err);
        }
    }

    // ======================== API CALLS ========================

    async apiCall(endpoint, method = 'GET', body = null, auth = true) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
            },
        };

        if (auth && this.token) {
            options.headers['Authorization'] = `Bearer ${this.token}`;
        }

        if (body) {
            options.body = JSON.stringify(body);
        }

        const response = await fetch(endpoint, options);
        const data = await response.json();

        if (!response.ok) {
            const err = new Error(data.error || data.detail || 'API Error');
            err.status = response.status;
            throw err;
        }

        return data;
    }

    // ======================== WEBSOCKET ========================

    initWebSocket() {
        if (!this.token) return;

        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const wsUrl = `${protocol}://${window.location.host}/ws?token=${this.token}`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('📡 WebSocket connected');
        };

        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.handleWebSocketMessage(message);
        };

        this.ws.onerror = (err) => {
            console.error('WebSocket error:', err);
        };

        this.ws.onclose = () => {
            console.log('📡 WebSocket disconnected');
            // Reconnect after 3 seconds
            setTimeout(() => this.initWebSocket(), 3000);
        };
    }

    handleWebSocketMessage(message) {
        console.log('📡 Message:', message);
        
        switch (message.type) {
            case 'client_updated':
                this.handleClientUpdated(message.data);
                break;
            case 'task_created':
                this.handleTaskCreated(message.data);
                break;
            case 'task_updated':
                this.handleTaskUpdated(message.data);
                break;
            case 'notification':
                this.showSuccess(message.data.message);
                break;
        }
    }

    handleClientUpdated(data) {
        const idx = this.clients.findIndex(c => c.id === data.id);
        if (idx >= 0) {
            Object.assign(this.clients[idx], data);
            this.render();
        }
    }

    handleTaskCreated(data) {
        // Reload tasks for current client if viewing details
        if (this.currentClientId) {
            this.loadClientDetails(this.currentClientId);
        }
    }

    handleTaskUpdated(data) {
        // Similar to created
        if (this.currentClientId) {
            this.loadClientDetails(this.currentClientId);
        }
    }

    // ======================== RENDERING ========================

    renderDashboard() {
        const app = document.getElementById('app');
        
        app.innerHTML = `
            <div class="dashboard">
                <nav class="navbar">
                    <div class="navbar-brand">🎛️ AM Hub</div>
                    <div class="navbar-menu">
                        <button onclick="dashboard.switchView('dashboard')" class="nav-item ${this.currentView === 'dashboard' ? 'active' : ''}">
                            📊 Dashboard
                        </button>
                        <button onclick="dashboard.switchView('clients')" class="nav-item ${this.currentView === 'clients' ? 'active' : ''}">
                            👥 Clients
                        </button>
                        <button onclick="dashboard.switchView('tasks')" class="nav-item ${this.currentView === 'tasks' ? 'active' : ''}">
                            📋 Tasks
                        </button>
                        <button onclick="dashboard.switchView('meetings')" class="nav-item ${this.currentView === 'meetings' ? 'active' : ''}">
                            📅 Meetings
                        </button>
                        <button onclick="dashboard.switchView('settings')" class="nav-item ${this.currentView === 'settings' ? 'active' : ''}">
                            ⚙️ Settings
                        </button>
                    </div>
                    <div class="navbar-right">
                        <span class="user-name">${this.user.name || this.user.email}</span>
                        <button onclick="dashboard.logout()" class="btn btn-ghost btn-sm">Logout</button>
                    </div>
                </nav>

                <main class="dashboard-main">
                    <div id="content-area"></div>
                </main>
            </div>
        `;

        this.addStyles();
        this.render();
    }

    switchView(view) {
        this.currentView = view;
        // Load additional data for specific views
        if (view === 'dashboard') {
            this.load360Dashboard().then(() => this.render());
        } else {
            this.render();
        }
    }

    render() {
        const content = document.getElementById('content-area');
        
        switch (this.currentView) {
            case 'dashboard':
                content.innerHTML = this.renderDashboardView();
                break;
            case 'clients':
                content.innerHTML = this.renderClientsView();
                break;
            case 'tasks':
                content.innerHTML = this.renderTasksView();
                break;
            case 'meetings':
                content.innerHTML = this.renderMeetingsView();
                break;
            case 'settings':
                content.innerHTML = this.renderSettingsView();
                break;
        }
    }

    renderDashboardView() {
        const { total_clients = 0, avg_health_score = 0, total_tasks = 0, open_tasks = 0, total_meetings = 0 } = this.stats;
        
        // Categorize clients by health
        const critical = this.clients.filter(c => (c.health_score || 0) < 50).length;
        const warning = this.clients.filter(c => (c.health_score || 0) >= 50 && (c.health_score || 0) < 75).length;
        const healthy = this.clients.filter(c => (c.health_score || 0) >= 75).length;
        
        // Categorize tasks by status
        const tasksByStatus = {
            done: 0,
            in_progress: 0,
            plan: 0,
            blocked: 0
        };
        
        return `
            <div class="dashboard-360">
                <!-- ГЛАВНАЯ ПЛИТКА - ОБЩИЙ СТАТУС -->
                <div class="hero-section-360">
                    <div class="hero-title">🎛️ 360° Account Overview</div>
                    <p class="hero-subtitle">Real-time view of all accounts and their status</p>
                </div>

                <!-- TOP KPIs - 5 главных показателей -->
                <div class="kpi-row-360">
                    <div class="kpi-card-360">
                        <div class="kpi-icon">👥</div>
                        <div class="kpi-content">
                            <div class="kpi-value-360">${total_clients}</div>
                            <div class="kpi-label-360">Total Clients</div>
                        </div>
                    </div>
                    <div class="kpi-card-360">
                        <div class="kpi-icon">💚</div>
                        <div class="kpi-content">
                            <div class="kpi-value-360" style="color: #10b981;">${avg_health_score.toFixed(1)}%</div>
                            <div class="kpi-label-360">Health Score</div>
                        </div>
                    </div>
                    <div class="kpi-card-360">
                        <div class="kpi-icon">📋</div>
                        <div class="kpi-content">
                            <div class="kpi-value-360">${total_tasks}</div>
                            <div class="kpi-label-360">Total Tasks</div>
                        </div>
                    </div>
                    <div class="kpi-card-360">
                        <div class="kpi-icon">⚡</div>
                        <div class="kpi-content">
                            <div class="kpi-value-360" style="color: #f59e0b;">${open_tasks}</div>
                            <div class="kpi-label-360">In Progress</div>
                        </div>
                    </div>
                    <div class="kpi-card-360">
                        <div class="kpi-icon">📅</div>
                        <div class="kpi-content">
                            <div class="kpi-value-360">${total_meetings}</div>
                            <div class="kpi-label-360">Meetings</div>
                        </div>
                    </div>
                </div>

                <!-- HEALTH BREAKDOWN - Визуальное распределение здоровья -->
                <div class="grid-360-2">
                    <!-- Health Status -->
                    <div class="card-360">
                        <div class="card-title-360">🏥 Health Status</div>
                        <div class="health-breakdown">
                            <div class="health-item">
                                <div class="health-bar health-bar-critical" style="width: ${healthy > 0 ? (healthy/total_clients*100) : 0}%"></div>
                                <span style="color: #10b981;">✓ Healthy: ${healthy}</span>
                            </div>
                            <div class="health-item">
                                <div class="health-bar health-bar-warning" style="width: ${warning > 0 ? (warning/total_clients*100) : 0}%"></div>
                                <span style="color: #f59e0b;">⚠ Warning: ${warning}</span>
                            </div>
                            <div class="health-item">
                                <div class="health-bar health-bar-danger" style="width: ${critical > 0 ? (critical/total_clients*100) : 0}%"></div>
                                <span style="color: #ef4444;">🔴 Critical: ${critical}</span>
                            </div>
                        </div>
                    </div>

                    <!-- Activity Gauge -->
                    <div class="card-360">
                        <div class="card-title-360">⚙️ Activity Gauge</div>
                        <div class="gauge-container">
                            <div class="gauge-circle" style="background: conic-gradient(#6366f1 0deg ${(avg_health_score/100)*360}deg, #e2e8f0 ${(avg_health_score/100)*360}deg 360deg)">
                                <div class="gauge-inner">
                                    <div class="gauge-value">${avg_health_score.toFixed(0)}%</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- CLIENT GRID - Все клиенты в виде плиток -->
                <div class="card-360 full-width">
                    <div class="card-title-360">👥 All Clients Status</div>
                    <div class="clients-360-grid">
                        ${this.clients.length > 0 ? this.clients.map((c, idx) => {
                            const health = c.health_score || 0;
                            const healthColor = this.getHealthColor(health);
                            const healthStatus = health >= 75 ? '✓' : health >= 50 ? '⚠' : '🔴';
                            return `
                                <div class="client-tile-360" onclick="dashboard.viewClientDetails(${c.id})" style="border-left: 4px solid ${healthColor};">
                                    <div class="client-tile-header">
                                        <div class="client-tile-name">${c.name}</div>
                                        <div class="health-score-small" style="background: ${healthColor};">${health}%</div>
                                    </div>
                                    <div class="client-tile-meta">
                                        <span class="badge-small">${c.segment || 'N/A'}</span>
                                        <span class="badge-small">${healthStatus}</span>
                                    </div>
                                    <div class="client-tile-stats">
                                        <span>📋 ${c.tasks_count || 0}</span>
                                        <span>📅 ${c.meetings_count || 0}</span>
                                    </div>
                                </div>
                            `;
                        }).join('') : '<p style="padding: 2rem; text-align: center; color: #94a3b8;">No clients yet</p>'}
                    </div>
                </div>

                <!-- BOTTOM INSIGHTS -->
                <div class="grid-360-2">
                    <!-- Top Performers -->
                    <div class="card-360">
                        <div class="card-title-360">🌟 Top Performers</div>
                        <div class="insights-list">
                            ${this.clients.sort((a, b) => (b.health_score || 0) - (a.health_score || 0)).slice(0, 3).map((c, idx) => `
                                <div class="insight-item">
                                    <span class="insight-rank">#${idx + 1}</span>
                                    <span class="insight-name">${c.name}</span>
                                    <span class="insight-value" style="color: ${this.getHealthColor(c.health_score)};">${c.health_score || 0}%</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>

                    <!-- Needs Attention -->
                    <div class="card-360">
                        <div class="card-title-360">⚠️ Needs Attention</div>
                        <div class="insights-list">
                            ${this.clients.filter(c => (c.health_score || 0) < 60).slice(0, 3).map((c, idx) => `
                                <div class="insight-item">
                                    <span class="insight-rank">!</span>
                                    <span class="insight-name">${c.name}</span>
                                    <span class="insight-value" style="color: ${this.getHealthColor(c.health_score)};">${c.health_score || 0}%</span>
                                </div>
                            `).join('') || '<p style="color: #94a3b8; font-size: 0.9rem;">All healthy!</p>'}
                        </div>
                    </div>
                </div>

                <!-- QUICK ACTIONS -->
                <div class="card-360 full-width">
                    <div class="card-title-360">🚀 Quick Actions</div>
                    <div class="action-buttons-360">
                        <button onclick="dashboard.switchView('clients'); dashboard.showCreateClientForm()" class="action-btn-360 btn-primary-360">
                            ➕ Add New Client
                        </button>
                        <button onclick="dashboard.switchView('tasks')" class="action-btn-360 btn-secondary-360">
                            📋 View All Tasks
                        </button>
                        <button onclick="dashboard.switchView('meetings')" class="action-btn-360 btn-secondary-360">
                            📅 Schedule Meeting
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    renderClientsView() {
        return `
            <div class="view-header">
                <div>
                    <h1>👥 Clients</h1>
                    <p>Manage your clients and accounts</p>
                </div>
                <button onclick="dashboard.showCreateClientForm()" class="btn btn-primary">
                    ➕ Add New Client
                </button>
            </div>

            <div id="create-client-form" style="display: none;">
                <div class="form-card">
                    <h3>New Client</h3>
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="client-name" placeholder="Client Name" />
                    </div>
                    <div class="form-group">
                        <label>Email</label>
                        <input type="email" id="client-email" placeholder="client@example.com" />
                    </div>
                    <div class="form-group">
                        <label>Phone</label>
                        <input type="tel" id="client-phone" placeholder="+1 (555) 123-4567" />
                    </div>
                    <div class="form-group">
                        <label>Segment</label>
                        <select id="client-segment">
                            <option value="smb">SMB</option>
                            <option value="mid-market">Mid-Market</option>
                            <option value="enterprise">Enterprise</option>
                        </select>
                    </div>
                    <div class="form-actions">
                        <button onclick="dashboard.createClient()" class="btn btn-primary">Create</button>
                        <button onclick="dashboard.hideCreateClientForm()" class="btn btn-ghost">Cancel</button>
                    </div>
                </div>
            </div>

            <div class="clients-grid">
                ${this.clients.length > 0 ? this.clients.map(c => `
                    <div class="client-card" onclick="dashboard.viewClientDetails(${c.id})">
                        <div class="client-header">
                            <h3>${c.name}</h3>
                            <div class="health-score" style="background: ${this.getHealthColor(c.health_score)}">
                                ${c.health_score || 0}%
                            </div>
                        </div>
                        <div class="client-details">
                            <p><strong>Email:</strong> ${c.email || 'N/A'}</p>
                            <p><strong>Segment:</strong> ${c.segment || 'N/A'}</p>
                            <p><strong>Manager:</strong> ${c.manager_email || 'N/A'}</p>
                        </div>
                        <div class="client-stats">
                            <span>📋 ${c.tasks_count || 0} tasks</span>
                            <span>📅 ${c.meetings_count || 0} meetings</span>
                        </div>
                    </div>
                `).join('') : '<p>No clients yet. Create one to get started!</p>'}
            </div>
        `;
    }

    renderTasksView() {
        return `
            <div class="view-header">
                <h1>📋 Tasks</h1>
                <p>Manage and track all tasks</p>
            </div>

            <div class="tasks-container">
                <h2>All Tasks</h2>
                <p>Select a client to manage tasks</p>
            </div>
        `;
    }

    renderMeetingsView() {
        return `
            <div class="view-header">
                <h1>📅 Meetings</h1>
                <p>Schedule and manage meetings</p>
            </div>

            <div class="meetings-container">
                <h2>All Meetings</h2>
                <p>Select a client to schedule meetings</p>
            </div>
        `;
    }

    renderSettingsView() {
        return `
            <div class="view-header">
                <h1>⚙️ Settings</h1>
                <p>Manage your account settings</p>
            </div>

            <div class="settings-container">
                <div class="settings-section">
                    <h2>👤 Profile</h2>
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="settings-name" value="${this.user.name || ''}" />
                    </div>
                    <div class="form-group">
                        <label>Email</label>
                        <input type="email" value="${this.user.email}" disabled />
                    </div>
                    <button onclick="dashboard.updateProfile()" class="btn btn-primary">
                        Save Changes
                    </button>
                </div>

                <div class="settings-section">
                    <h2>🔐 Security</h2>
                    <button class="btn btn-secondary">Change Password</button>
                </div>
            </div>
        `;
    }

    viewClientDetails(clientId) {
        this.currentClientId = clientId;
        // Load and display client details
        this.loadClientDetails(clientId).then(details => {
            const content = document.getElementById('content-area');
            content.innerHTML = this.renderClientDetailsView(details);
        });
    }

    renderClientDetailsView(details) {
        const { client, tasks, meetings } = details;

        return `
            <div class="view-header">
                <button onclick="dashboard.switchView('clients')" class="btn btn-ghost">← Back</button>
                <h1>${client.name}</h1>
            </div>

            <div class="client-details-grid">
                <div class="details-card">
                    <h3>📊 Info</h3>
                    <p><strong>Email:</strong> ${client.email}</p>
                    <p><strong>Phone:</strong> ${client.phone || 'N/A'}</p>
                    <p><strong>Segment:</strong> ${client.segment}</p>
                    <p><strong>Health Score:</strong> <span style="color: ${this.getHealthColor(client.health_score)}">${client.health_score}%</span></p>
                </div>

                <div class="details-card">
                    <h3>📋 Tasks (${tasks.length})</h3>
                    ${tasks.slice(0, 5).map(t => `
                        <div class="task-item">
                            <strong>${t.title}</strong>
                            <span class="badge badge-${t.status}">${t.status}</span>
                        </div>
                    `).join('')}
                </div>

                <div class="details-card">
                    <h3>📅 Meetings (${meetings.length})</h3>
                    ${meetings.slice(0, 5).map(m => `
                        <div class="meeting-item">
                            <strong>${m.meeting_type}</strong>
                            <span>${new Date(m.meeting_date).toLocaleDateString()}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    // ======================== ACTIONS ========================

    showCreateClientForm() {
        const form = document.getElementById('create-client-form');
        if (form) form.style.display = 'block';
    }

    hideCreateClientForm() {
        const form = document.getElementById('create-client-form');
        if (form) form.style.display = 'none';
    }

    async createClient() {
        const name = document.getElementById('client-name')?.value;
        const email = document.getElementById('client-email')?.value;
        const phone = document.getElementById('client-phone')?.value;
        const segment = document.getElementById('client-segment')?.value;

        if (!name || !email) {
            this.showError('Please fill required fields');
            return;
        }

        try {
            const client = await this.apiCall('/api/clients', 'POST', {
                name,
                email,
                phone,
                segment,
            });

            this.clients.push(client);
            this.hideCreateClientForm();
            this.render();
            this.showSuccess('Client created successfully!');
        } catch (err) {
            this.showError('Failed to create client: ' + err.message);
        }
    }

    async updateProfile() {
        const name = document.getElementById('settings-name')?.value;

        if (!name) {
            this.showError('Name is required');
            return;
        }

        try {
            const updated = await this.apiCall('/api/me', 'PUT', { name });
            this.user = updated;
            this.showSuccess('Profile updated!');
        } catch (err) {
            this.showError('Failed to update profile');
        }
    }

    // ======================== UTILITIES ========================

    getHealthColor(score) {
        if (score >= 80) return '#10b981'; // green
        if (score >= 60) return '#f59e0b'; // yellow
        return '#ef4444'; // red
    }

    setupEventListeners() {
        // Global keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                this.showCommandPalette();
            }
        });
    }

    showCommandPalette() {
        // Simple command palette
        console.log('Command palette not implemented');
    }

    showError(message) {
        console.error(message);
        const alert = this.createAlert(message, 'error');
        this.showAlert(alert);
    }

    showSuccess(message) {
        console.log(message);
        const alert = this.createAlert(message, 'success');
        this.showAlert(alert);
    }

    createAlert(message, type) {
        const alert = document.createElement('div');
        alert.className = `alert alert-${type}`;
        alert.textContent = message;
        return alert;
    }

    showAlert(alert) {
        const container = document.querySelector('.alert-container') || this.createAlertContainer();
        container.appendChild(alert);
        setTimeout(() => alert.remove(), 3000);
    }

    createAlertContainer() {
        const container = document.createElement('div');
        container.className = 'alert-container';
        document.body.appendChild(container);
        return container;
    }

    addStyles() {
        const style = document.createElement('style');
        style.textContent = `
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: #f8fafc;
                color: #1e293b;
            }

            .dashboard {
                display: flex;
                flex-direction: column;
                min-height: 100vh;
            }

            /* Navigation */
            .navbar {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 1rem 2rem;
                background: white;
                border-bottom: 1px solid #e2e8f0;
                gap: 2rem;
            }

            .navbar-brand {
                font-size: 1.5rem;
                font-weight: bold;
                color: #6366f1;
            }

            .navbar-menu {
                display: flex;
                gap: 1rem;
                flex: 1;
            }

            .nav-item {
                padding: 0.5rem 1rem;
                background: none;
                border: none;
                border-radius: 6px;
                cursor: pointer;
                font-size: 0.95rem;
                color: #64748b;
                transition: all 0.2s ease;
            }

            .nav-item:hover {
                background: #f1f5f9;
                color: #1e293b;
            }

            .nav-item.active {
                background: #6366f1;
                color: white;
            }

            .navbar-right {
                display: flex;
                align-items: center;
                gap: 1rem;
            }

            .user-name {
                font-weight: 500;
                color: #1e293b;
            }

            /* Main Content */
            .dashboard-main {
                flex: 1;
                padding: 2rem;
                overflow-y: auto;
            }

            /* 360 DASHBOARD STYLES */
            .dashboard-360 {
                display: flex;
                flex-direction: column;
                gap: 1.5rem;
            }

            .hero-section-360 {
                padding: 2rem;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 16px;
                color: white;
                text-align: center;
            }

            .hero-title {
                font-size: 2rem;
                font-weight: bold;
                margin-bottom: 0.5rem;
            }

            .hero-subtitle {
                font-size: 1rem;
                opacity: 0.9;
            }

            /* KPI Row */
            .kpi-row-360 {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 1rem;
            }

            .kpi-card-360 {
                background: white;
                padding: 1.5rem;
                border-radius: 12px;
                border: 1px solid #e2e8f0;
                display: flex;
                align-items: center;
                gap: 1rem;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                transition: all 0.2s ease;
            }

            .kpi-card-360:hover {
                border-color: #6366f1;
                box-shadow: 0 4px 12px rgba(99, 102, 241, 0.15);
            }

            .kpi-icon {
                font-size: 2rem;
            }

            .kpi-content {
                display: flex;
                flex-direction: column;
            }

            .kpi-value-360 {
                font-size: 1.8rem;
                font-weight: bold;
                color: #6366f1;
                line-height: 1;
            }

            .kpi-label-360 {
                font-size: 0.75rem;
                color: #64748b;
                margin-top: 0.25rem;
                font-weight: 500;
            }

            /* Grid Layouts */
            .grid-360-2 {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 1.5rem;
            }

            .card-360 {
                background: white;
                padding: 1.5rem;
                border-radius: 12px;
                border: 1px solid #e2e8f0;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }

            .card-360.full-width {
                grid-column: 1 / -1;
            }

            .card-title-360 {
                font-size: 1.1rem;
                font-weight: 600;
                color: #1e293b;
                margin-bottom: 1rem;
            }

            /* Health Breakdown */
            .health-breakdown {
                display: flex;
                flex-direction: column;
                gap: 1rem;
            }

            .health-item {
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }

            .health-bar {
                flex: 1;
                height: 24px;
                border-radius: 6px;
                transition: width 0.3s ease;
                min-width: 20px;
            }

            .health-bar-critical {
                background: linear-gradient(90deg, #10b981, #34d399);
            }

            .health-bar-warning {
                background: linear-gradient(90deg, #f59e0b, #fbbf24);
            }

            .health-bar-danger {
                background: linear-gradient(90deg, #ef4444, #f87171);
            }

            /* Gauge */
            .gauge-container {
                display: flex;
                justify-content: center;
                padding: 1rem 0;
            }

            .gauge-circle {
                width: 120px;
                height: 120px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: all 0.3s ease;
            }

            .gauge-inner {
                width: 100px;
                height: 100px;
                border-radius: 50%;
                background: white;
                display: flex;
                align-items: center;
                justify-content: center;
                flex-direction: column;
            }

            .gauge-value {
                font-size: 1.8rem;
                font-weight: bold;
                color: #6366f1;
            }

            /* Clients Grid 360 */
            .clients-360-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                gap: 1rem;
            }

            .client-tile-360 {
                background: #f8fafc;
                padding: 1rem;
                border-radius: 10px;
                border: 1px solid #e2e8f0;
                cursor: pointer;
                transition: all 0.2s ease;
            }

            .client-tile-360:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(99, 102, 241, 0.2);
                background: white;
            }

            .client-tile-header {
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                margin-bottom: 0.75rem;
            }

            .client-tile-name {
                font-weight: 600;
                color: #1e293b;
                font-size: 0.95rem;
                flex: 1;
            }

            .health-score-small {
                width: 40px;
                height: 40px;
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
                font-size: 0.85rem;
                flex-shrink: 0;
            }

            .client-tile-meta {
                display: flex;
                gap: 0.5rem;
                margin-bottom: 0.75rem;
            }

            .badge-small {
                font-size: 0.7rem;
                padding: 2px 6px;
                border-radius: 4px;
                background: #e2e8f0;
                color: #64748b;
                font-weight: 500;
            }

            .client-tile-stats {
                display: flex;
                gap: 0.75rem;
                font-size: 0.8rem;
                color: #64748b;
            }

            /* Insights */
            .insights-list {
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
            }

            .insight-item {
                display: flex;
                align-items: center;
                gap: 0.75rem;
                padding: 0.75rem;
                background: #f8fafc;
                border-radius: 8px;
            }

            .insight-rank {
                width: 32px;
                height: 32px;
                border-radius: 6px;
                background: #6366f1;
                color: white;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
                font-size: 0.85rem;
                flex-shrink: 0;
            }

            .insight-name {
                flex: 1;
                font-weight: 500;
                color: #1e293b;
            }

            .insight-value {
                font-weight: bold;
                font-size: 0.95rem;
            }

            /* Action Buttons */
            .action-buttons-360 {
                display: flex;
                gap: 1rem;
                flex-wrap: wrap;
            }

            .action-btn-360 {
                padding: 0.75rem 1.5rem;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 500;
                transition: all 0.2s ease;
                font-size: 0.95rem;
            }

            .btn-primary-360 {
                background: #6366f1;
                color: white;
            }

            .btn-primary-360:hover {
                background: #4f46e5;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
            }

            .btn-secondary-360 {
                background: #e2e8f0;
                color: #1e293b;
            }

            .btn-secondary-360:hover {
                background: #cbd5e1;
                transform: translateY(-2px);
            }

            /* View */
            .view-header {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                margin-bottom: 2rem;
            }

            .view-header h1 {
                font-size: 2rem;
                color: #1e293b;
                margin-bottom: 0.5rem;
            }

            .view-header p {
                color: #64748b;
            }

            /* KPI Grid */
            .kpi-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 1rem;
                margin-bottom: 2rem;
            }

            .kpi-card {
                background: white;
                padding: 1.5rem;
                border-radius: 12px;
                border: 1px solid #e2e8f0;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }

            .kpi-value {
                font-size: 2.5rem;
                font-weight: bold;
                color: #6366f1;
            }

            .kpi-label {
                color: #64748b;
                margin-top: 0.5rem;
                font-size: 0.9rem;
            }

            /* Dashboard Grid */
            .dashboard-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 1.5rem;
            }

            .dashboard-section {
                background: white;
                padding: 1.5rem;
                border-radius: 12px;
                border: 1px solid #e2e8f0;
            }

            .dashboard-section h2 {
                font-size: 1.1rem;
                color: #1e293b;
                margin-bottom: 1rem;
            }

            /* Client List */
            .client-list-mini {
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
            }

            .client-item-mini {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 0.75rem;
                background: #f8fafc;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.2s ease;
            }

            .client-item-mini:hover {
                background: #e2e8f0;
            }

            .client-info-mini {
                display: flex;
                flex-direction: column;
                gap: 0.25rem;
            }

            .client-info-mini strong {
                color: #1e293b;
            }

            .client-segment {
                font-size: 0.75rem;
                color: #64748b;
            }

            .health-badge {
                width: 50px;
                height: 50px;
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
            }

            /* Buttons */
            .btn {
                padding: 0.75rem 1.5rem;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-size: 0.95rem;
                font-weight: 500;
                transition: all 0.2s ease;
            }

            .btn-primary {
                background: #6366f1;
                color: white;
            }

            .btn-primary:hover {
                background: #4f46e5;
            }

            .btn-secondary {
                background: #e2e8f0;
                color: #1e293b;
            }

            .btn-secondary:hover {
                background: #cbd5e1;
            }

            .btn-ghost {
                background: none;
                color: #6366f1;
                border: 1px solid #6366f1;
            }

            .btn-ghost:hover {
                background: #f0f4ff;
            }

            .btn-sm {
                padding: 0.5rem 1rem;
                font-size: 0.85rem;
            }

            .btn-block {
                width: 100%;
            }

            /* Quick Actions */
            .quick-actions {
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
            }

            .quick-actions .btn {
                text-align: left;
            }

            /* Clients Grid */
            .clients-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 1.5rem;
            }

            .client-card {
                background: white;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                padding: 1.5rem;
                cursor: pointer;
                transition: all 0.2s ease;
            }

            .client-card:hover {
                border-color: #6366f1;
                box-shadow: 0 4px 12px rgba(99, 102, 241, 0.15);
            }

            .client-header {
                display: flex;
                justify-content: space-between;
                align-items: start;
                margin-bottom: 1rem;
            }

            .client-header h3 {
                font-size: 1.1rem;
                color: #1e293b;
            }

            .health-score {
                width: 60px;
                height: 60px;
                border-radius: 10px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
                font-size: 0.95rem;
            }

            .client-details {
                margin-bottom: 1rem;
            }

            .client-details p {
                font-size: 0.9rem;
                color: #64748b;
                margin-bottom: 0.5rem;
            }

            .client-stats {
                display: flex;
                gap: 1rem;
                font-size: 0.85rem;
                color: #64748b;
            }

            /* Forms */
            .form-card {
                background: white;
                padding: 1.5rem;
                border-radius: 12px;
                border: 1px solid #e2e8f0;
                margin-bottom: 1.5rem;
            }

            .form-card h3 {
                font-size: 1.1rem;
                color: #1e293b;
                margin-bottom: 1rem;
            }

            .form-group {
                margin-bottom: 1rem;
            }

            .form-group label {
                display: block;
                font-weight: 500;
                color: #1e293b;
                margin-bottom: 0.5rem;
            }

            .form-group input,
            .form-group select {
                width: 100%;
                padding: 0.75rem;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                font-size: 0.95rem;
            }

            .form-group input:focus,
            .form-group select:focus {
                outline: none;
                border-color: #6366f1;
                box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
            }

            .form-actions {
                display: flex;
                gap: 1rem;
                margin-top: 1.5rem;
            }

            /* Alerts */
            .alert-container {
                position: fixed;
                top: 2rem;
                right: 2rem;
                z-index: 1000;
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
            }

            .alert {
                padding: 1rem 1.5rem;
                border-radius: 8px;
                color: white;
                font-weight: 500;
                animation: slideIn 0.3s ease;
            }

            .alert-success {
                background: #10b981;
            }

            .alert-error {
                background: #ef4444;
            }

            @keyframes slideIn {
                from {
                    transform: translateX(400px);
                    opacity: 0;
                }
                to {
                    transform: translateX(0);
                    opacity: 1;
                }
            }

            /* Badges */
            .badge {
                display: inline-block;
                padding: 0.25rem 0.75rem;
                border-radius: 20px;
                font-size: 0.75rem;
                font-weight: 500;
            }

            .badge-plan {
                background: #dbeafe;
                color: #1e40af;
            }

            .badge-in_progress {
                background: #fef08a;
                color: #854d0e;
            }

            .badge-done {
                background: #dcfce7;
                color: #166534;
            }

            /* Responsive */
            @media (max-width: 768px) {
                .navbar {
                    flex-direction: column;
                    align-items: flex-start;
                    padding: 1rem;
                }

                .navbar-menu {
                    width: 100%;
                    overflow-x: auto;
                }

                .dashboard-main {
                    padding: 1rem;
                }

                .kpi-grid {
                    grid-template-columns: repeat(2, 1fr);
                }

                .clients-grid {
                    grid-template-columns: 1fr;
                }

                .kpi-row-360 {
                    grid-template-columns: repeat(2, 1fr);
                }

                .clients-360-grid {
                    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                }

                .action-buttons-360 {
                    flex-direction: column;
                }

                .action-btn-360 {
                    width: 100%;
                }
            }
        `;
        document.head.appendChild(style);
    }
}

// Initialize dashboard when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new Dashboard();
});
