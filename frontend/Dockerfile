FROM nginx:1.27-alpine
COPY index.html usecases.html erd.js /usr/share/nginx/html/
EXPOSE 8080
CMD sed -i 's/listen\s*80;/listen 8080;/' /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'
