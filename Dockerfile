FROM python:3.13-slim
WORKDIR /app
COPY server.py ./
COPY dist ./dist
RUN mkdir /data && useradd --uid 10001 --create-home wing && chown wing:wing /data
USER wing
ENV PORT=8040 WING_DB=/data/wing-night.sqlite PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8040
CMD ["python", "server.py"]
