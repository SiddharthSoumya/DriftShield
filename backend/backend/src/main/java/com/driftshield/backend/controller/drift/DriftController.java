package com.driftshield.backend.controller.drift;

import com.driftshield.backend.detection.drift.DriftDetectionEngine;
import com.driftshield.backend.dto.response.DriftDifference;
import com.driftshield.backend.dto.response.DriftEventResponse;
import com.driftshield.backend.service.drift.DriftEventService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequiredArgsConstructor
public class DriftController {

    private final DriftDetectionEngine driftDetectionEngine;
    private final DriftEventService driftEventService;

    @GetMapping("/api/drift/detect")
    public List<DriftDifference> detectDrift(){
        return driftDetectionEngine.detectDrift();
    }

    @GetMapping("/api/drift/events")
    public List<DriftEventResponse> getAllEvents(){
        return driftEventService.getAllEvents();
    }
}
