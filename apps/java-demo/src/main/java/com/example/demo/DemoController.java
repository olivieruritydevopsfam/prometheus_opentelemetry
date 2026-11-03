package com.example.demo;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.LongCounter;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class DemoController {

    private static final AttributeKey<String> PRODUCT = AttributeKey.stringKey("product");
    private static final List<String> PRODUCTS = List.of("book", "laptop", "phone");

    // Avec le javaagent, GlobalOpenTelemetry renvoie le SDK configuré par l'agent :
    // ce compteur part dans le même flux OTLP que les métriques auto-instrumentées.
    private final LongCounter ordersCreated = GlobalOpenTelemetry.getMeter("com.example.demo")
            .counterBuilder("orders.created")
            .setDescription("Nombre de commandes créées")
            .setUnit("{order}")
            .build();

    @GetMapping("/hello")
    public Map<String, String> hello() {
        return Map.of("message", "hello");
    }

    @GetMapping("/slow")
    public Map<String, Object> slow() throws InterruptedException {
        long delayMs = ThreadLocalRandom.current().nextLong(200, 2001);
        Thread.sleep(delayMs);
        return Map.of("message", "slow", "delayMs", delayMs);
    }

    @GetMapping("/error")
    public Map<String, String> error() {
        if (ThreadLocalRandom.current().nextInt(3) == 0) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "erreur simulée");
        }
        return Map.of("message", "ok");
    }

    @GetMapping("/order")
    public Map<String, String> order() {
        String product = PRODUCTS.get(ThreadLocalRandom.current().nextInt(PRODUCTS.size()));
        ordersCreated.add(1, Attributes.of(PRODUCT, product));
        return Map.of("message", "order created", "product", product);
    }
}
