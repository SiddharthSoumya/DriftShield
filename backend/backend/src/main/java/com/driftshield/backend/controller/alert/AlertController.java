package com.driftshield.backend.controller.alert;


import com.driftshield.backend.dto.request.AlertRequest;
import com.driftshield.backend.dto.response.AlertResponse;
import com.driftshield.backend.service.alert.AlertService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/alerts")
@RequiredArgsConstructor
public class AlertController {
    private final AlertService alertService;

    @PostMapping
    public AlertResponse createAlert(
            @RequestBody AlertRequest request) {
        return alertService.createAlert(request);
    }

    @GetMapping
    public List<AlertResponse> getAllAlerts(){
        return alertService.getAllAlerts();
    }

}
