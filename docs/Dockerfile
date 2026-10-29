# Landing page de Kalmiya como sitio estático (nginx).
# Easypanel: App → Source: este repo de GitHub → Build: Dockerfile, ruta de build "docs" → puerto 80.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/
COPY assets /usr/share/nginx/html/assets
EXPOSE 80
