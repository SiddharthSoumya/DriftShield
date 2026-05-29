package com.driftshield.backend.controller.runtime;

import com.driftshield.backend.service.runtime.RuntimeConfigService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequiredArgsConstructor
public class RuntimeConfigController {

    private final RuntimeConfigService runtimeConfigService;

    @GetMapping("/api/runtime-config")
    public Map<String, Object> getRuntimeConfig() {

        return runtimeConfigService.getRuntimeConfig();
    }
}