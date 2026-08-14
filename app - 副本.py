#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import requests
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, g, abort, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from functools import wraps

# ---------- 配置 ----------
BASE_DOMAIN = ''
BLOG_TARGET = ''
CLOUDFLARE_ZONE_ID = ''
CLOUDFLARE_API_TOKEN = ''
ADMIN_PASSWORD = ''
SECRET_KEY = ''

# ---------- 默认设置 ----------
DEFAULT_SETTINGS = {
    'site_title': '个人博客',
    'bio': '您好，我是 ，热爱编程与技术分享。欢迎访问我的个人博客。',
    'video_url': 'https://space.bilibili.com',
    'github_url': '',
    'footer_text': '© 2026 个人博客',
    'bg_effect': 'matrix',
    'extra_links': '[]',
    'sites_list': '[{"name":"聊天网站","url":""}]',
    'admin_email': ''   # 新增：管理员邮箱
}

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = SECRET_KEY
db = SQLAlchemy(app)

# ---------- 模型 ----------
class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    project_type = db.Column(db.String(20), nullable=False)
    download_url = db.Column(db.String(200), nullable=True)
    repo_url = db.Column(db.String(200), nullable=True)
    website_url = db.Column(db.String(200), nullable=True)
    subdomain_prefix = db.Column(db.String(50), unique=True, nullable=True)
    template_type = db.Column(db.String(20), default='default')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def full_subdomain(self):
        if self.subdomain_prefix:
            return f"{self.subdomain_prefix}.{BASE_DOMAIN}"
        return None

class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)

    @staticmethod
    def get(key, default=None):
        entry = Config.query.filter_by(key=key).first()
        return entry.value if entry else default

    @staticmethod
    def set(key, value):
        entry = Config.query.filter_by(key=key).first()
        if entry:
            entry.value = value
        else:
            entry = Config(key=key, value=value)
            db.session.add(entry)
        db.session.commit()

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ---------- 辅助函数 ----------
def init_default_settings():
    for key, value in DEFAULT_SETTINGS.items():
        if not Config.query.filter_by(key=key).first():
            Config.set(key, value)

def add_column_if_not_exists():
    conn = sqlite3.connect('blog.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(project)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'website_url' not in columns:
        cursor.execute("ALTER TABLE project ADD COLUMN website_url VARCHAR(200)")
        conn.commit()
    # 确保 message 表存在（SQLAlchemy 会自动创建，但以防万一）
    conn.close()

# ---------- 上下文处理器 ----------
@app.context_processor
def inject_globals():
    extra_links_raw = Config.get('extra_links', '[]')
    try:
        extra_links = json.loads(extra_links_raw)
    except:
        extra_links = []
    sites_list_raw = Config.get('sites_list', '[]')
    try:
        sites_list = json.loads(sites_list_raw)
    except:
        sites_list = []
    return {
        'site_title': Config.get('site_title', '个人博客'),
        'bio': Config.get('bio', ''),
        'video_url': Config.get('video_url', '#'),
        'github_url': Config.get('github_url', '#'),
        'footer_text': Config.get('footer_text', ''),
        'bg_effect': Config.get('bg_effect', 'matrix'),
        'extra_links': extra_links,
        'sites_list': sites_list,
        'admin_email': Config.get('admin_email', 'admin@example.com'),
        'BASE_DOMAIN': BASE_DOMAIN
    }

# ---------- Cloudflare DNS ----------
class CloudflareDNS:
    BASE_URL = 'https://api.cloudflare.com/client/v4'
    def __init__(self):
        self.zone_id = CLOUDFLARE_ZONE_ID
        self.headers = {'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}', 'Content-Type': 'application/json'}
    def add_cname_record(self, name, target):
        resp = requests.post(f"{self.BASE_URL}/zones/{self.zone_id}/dns_records",
                             headers=self.headers, json={"type":"CNAME","name":name,"content":target,"ttl":120,"proxied":True})
        return resp.json()
    def delete_record_by_name(self, name):
        list_resp = requests.get(f"{self.BASE_URL}/zones/{self.zone_id}/dns_records?name={name}", headers=self.headers)
        result = list_resp.json()
        if result['success'] and result['result']:
            record_id = result['result'][0]['id']
            return requests.delete(f"{self.BASE_URL}/zones/{self.zone_id}/dns_records/{record_id}", headers=self.headers).json()
        return {'success': False}

def sync_dns(project, action='add'):
    if not project.subdomain_prefix:
        return
    full = project.full_subdomain()
    if not full:
        return
    cf = CloudflareDNS()
    if action == 'add':
        cf.delete_record_by_name(full)
        cf.add_cname_record(full, BLOG_TARGET)
    elif action == 'delete':
        cf.delete_record_by_name(full)

# ---------- 登录装饰器 ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ---------- 中间件 ----------
@app.before_request
def detect_subdomain():
    host = request.host.split(':')[0]
    if host.endswith(BASE_DOMAIN) and host != BASE_DOMAIN and host != f'www.{BASE_DOMAIN}':
        prefix = host.replace(f'.{BASE_DOMAIN}', '')
        project = Project.query.filter_by(subdomain_prefix=prefix, is_active=True).first()
        g.subdomain_project = project if project else None
    else:
        g.subdomain_project = None

# ---------- 前台 ----------
@app.route('/')
def home():
    if hasattr(g, 'subdomain_project') and g.subdomain_project:
        return render_template('index.html', page='detail', project=g.subdomain_project)
    opensource = Project.query.filter_by(project_type='opensource', is_active=True).all()
    software = Project.query.filter_by(project_type='software', is_active=True).all()
    return render_template('index.html', page='home',
                          opensource_projects=opensource, software_projects=software)

@app.route('/project/<int:project_id>')
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    if not project.is_active:
        abort(404)
    return render_template('index.html', page='detail', project=project)

@app.route('/sites')
def sites():
    all_projects = Project.query.filter_by(is_active=True).order_by(Project.created_at.desc()).all()
    return render_template('index.html', page='sites', all_projects=all_projects)

# ---------- 留言板 ----------
@app.route('/guestbook', methods=['GET', 'POST'])
def guestbook():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        content = request.form.get('content', '').strip()
        if name and email and content:
            msg = Message(name=name, email=email, content=content)
            db.session.add(msg)
            db.session.commit()
            return redirect(url_for('guestbook'))
    messages = Message.query.order_by(Message.created_at.desc()).all()
    return render_template('index.html', page='guestbook', messages=messages)

# ---------- 后台登录 ----------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('index.html', page='admin_login', error='密码错误')
    return render_template('index.html', page='admin_login')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

# ---------- 项目管理 ----------
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            project = Project(
                title=request.form['title'],
                description=request.form['description'],
                project_type=request.form['project_type'],
                download_url=request.form.get('download_url', ''),
                repo_url=request.form.get('repo_url', ''),
                website_url=request.form.get('website_url', ''),
                subdomain_prefix=request.form.get('subdomain_prefix', '') or None,
                template_type=request.form.get('template_type', 'default'),
                is_active=True
            )
            db.session.add(project)
            db.session.commit()
            sync_dns(project, 'add')
            return redirect(url_for('project_detail', project_id=project.id))
        elif action == 'edit':
            project_id = request.form['id']
            project = Project.query.get(project_id)
            if project:
                old_prefix = project.subdomain_prefix
                project.title = request.form['title']
                project.description = request.form['description']
                project.project_type = request.form['project_type']
                project.download_url = request.form.get('download_url', '')
                project.repo_url = request.form.get('repo_url', '')
                project.website_url = request.form.get('website_url', '')
                new_prefix = request.form.get('subdomain_prefix', '') or None
                project.subdomain_prefix = new_prefix
                project.template_type = request.form.get('template_type', 'default')
                project.is_active = 'is_active' in request.form
                db.session.commit()
                if old_prefix != new_prefix:
                    if old_prefix:
                        old_proj = Project()
                        old_proj.subdomain_prefix = old_prefix
                        sync_dns(old_proj, 'delete')
                    if new_prefix and project.is_active:
                        sync_dns(project, 'add')
                else:
                    if project.is_active:
                        sync_dns(project, 'add')
                    else:
                        sync_dns(project, 'delete')
                return redirect(url_for('admin_dashboard'))
        elif action == 'delete':
            project_id = request.form['id']
            project = Project.query.get(project_id)
            if project:
                sync_dns(project, 'delete')
                db.session.delete(project)
                db.session.commit()
            return redirect(url_for('admin_dashboard'))

    projects = Project.query.order_by(Project.created_at.desc()).all()
    edit_id = request.args.get('edit')
    edit_project = Project.query.get(edit_id) if edit_id else None
    return render_template('index.html', page='admin', projects=projects, edit_project=edit_project)

# ---------- 留言管理 ----------
@app.route('/admin/messages')
@login_required
def admin_messages():
    messages = Message.query.order_by(Message.created_at.desc()).all()
    return render_template('index.html', page='admin_messages', messages=messages)

@app.route('/admin/message/delete/<int:msg_id>')
@login_required
def admin_message_delete(msg_id):
    msg = Message.query.get_or_404(msg_id)
    db.session.delete(msg)
    db.session.commit()
    return redirect(url_for('admin_messages'))

# ---------- 全局设置管理 ----------
@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    if request.method == 'POST':
        for key in ['site_title', 'bio', 'video_url', 'github_url', 'footer_text', 'bg_effect', 'extra_links', 'sites_list', 'admin_email']:
            value = request.form.get(key, '').strip()
            if key in ('extra_links', 'sites_list'):
                if not value:
                    value = '[]'
                else:
                    try:
                        json.loads(value)
                    except:
                        value = '[]'
            Config.set(key, value)
        return redirect(url_for('admin_settings'))
    settings = {}
    for key in ['site_title', 'bio', 'video_url', 'github_url', 'footer_text', 'bg_effect', 'extra_links', 'sites_list', 'admin_email']:
        settings[key] = Config.get(key, '')
    return render_template('index.html', page='settings', settings=settings)

# ---------- 域名管理 ----------
@app.route('/admin/domains', methods=['GET', 'POST'])
@login_required
def admin_domains():
    message = None
    if request.method == 'POST':
        action = request.form.get('action')
        project_id = request.form.get('project_id')
        project = Project.query.get(project_id) if project_id else None

        if action == 'add_dns' and project:
            sync_dns(project, 'add')
            message = f'已为项目 "{project.title}" 添加 DNS 记录'
        elif action == 'delete_dns' and project:
            sync_dns(project, 'delete')
            message = f'已删除项目 "{project.title}" 的 DNS 记录'
        elif action == 'rewrite_prefix':
            project = Project.query.get(request.form.get('project_id'))
            if project:
                new_prefix = request.form.get('new_prefix', '').strip()
                if new_prefix != project.subdomain_prefix:
                    if project.subdomain_prefix:
                        old_proj = Project()
                        old_proj.subdomain_prefix = project.subdomain_prefix
                        sync_dns(old_proj, 'delete')
                    project.subdomain_prefix = new_prefix if new_prefix else None
                    db.session.commit()
                    if project.subdomain_prefix and project.is_active:
                        sync_dns(project, 'add')
                    message = f'已改写项目 "{project.title}" 的前缀为 "{new_prefix}"'
                else:
                    message = '新前缀与旧前缀相同，未作改动'
        elif action == 'batch_sync':
            projects = Project.query.filter_by(is_active=True).filter(Project.subdomain_prefix.isnot(None)).all()
            for proj in projects:
                sync_dns(proj, 'add')
            message = f'已为 {len(projects)} 个项目重新同步 DNS'

    all_projects = Project.query.order_by(Project.created_at.desc()).all()
    return render_template('index.html', page='domains', projects=all_projects, message=message)

# ---------- 初始化 ----------
with app.app_context():
    db.create_all()
    init_default_settings()
    try:
        add_column_if_not_exists()
    except:
        pass

# ---------- 启动 ----------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
