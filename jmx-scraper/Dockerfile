# syntax=docker/dockerfile:1
# JMX scraper OpenTelemetry : lit les MBeans d'une JVM distante et pousse en OTLP.
FROM eclipse-temurin:21.0.8_9-jre

ARG JMX_SCRAPER_VERSION=1.61.0-alpha
WORKDIR /app

ADD --chmod=644 \
    https://repo1.maven.org/maven2/io/opentelemetry/contrib/opentelemetry-jmx-scraper/${JMX_SCRAPER_VERSION}/opentelemetry-jmx-scraper-${JMX_SCRAPER_VERSION}.jar \
    /app/jmx-scraper.jar

RUN useradd --system --uid 10001 app
USER app

COPY gc.yaml /etc/jmx-scraper/gc.yaml

# Configuration via les variables OTEL_JMX_* / OTEL_* (cf. docker-compose.yml).
# --add-exports supprime le warning "SASL unsupported" au démarrage.
ENTRYPOINT ["java", "--add-exports=java.security.sasl/com.sun.security.sasl=ALL-UNNAMED", "-jar", "/app/jmx-scraper.jar"]
